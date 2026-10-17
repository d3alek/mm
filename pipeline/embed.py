"""build/records → dist/semantic/: chunk talks, embed with bge-small-en-v1.5, write the browser index.

Needs the `embed` extra (onnxruntime, tokenizers, numpy): `uv run --extra embed python -m pipeline embed`.
Vectors are cached per talk in build/embed-cache/<key>.npy, keyed by model, chunking and cue text,
so a rebuild only embeds talks whose text changed.

Browser files (all row-aligned, N chunks):
  meta.json   model, query prefix, talk table [id, title, date, platform, series, year, provider, url]
  chunks.bin  Uint16 talk[N] | Uint16 start_s[N] | Uint16 end_s[N]
  bin.bin     N × 48 B sign bits (dim 8j+k → byte j, bit 7-k), for the Hamming scan
  i8.bin      N × 384 int8, each row scaled to max |127|, for re-ranking
"""

import hashlib
import json
import os
import time
import urllib.request

from .build_site import AT_TIME, platform_name, video_url, year

MODEL = "Xenova/bge-small-en-v1.5"
REVISION = "ea104dacec62c0de699686887e3f920caeb4f3e3"  # transformers.js loads the same revision
# The q8 file the browser uses for queries: 1.3x faster than fp32 on CPU, cosine 0.997 to fp32 on passages.
ONNX_FILE = "onnx/model_quantized.onnx"
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
DIMS = 384
TARGET_WORDS = 250
OVERLAP_WORDS = 50
MIN_TAIL_WORDS = 60
MAX_TOKENS = 512
CACHE_VERSION = 1


def chunk(cues: list[dict]) -> list[tuple[int, int]]:
    """Cue index ranges [i, j) of ~TARGET_WORDS words, consecutive chunks sharing ~OVERLAP_WORDS."""
    wc = [len(c["text"].split()) for c in cues]
    n, i, out = len(cues), 0, []
    while i < n:
        j, w = i, 0
        while j < n and w < TARGET_WORDS:
            w += wc[j]
            j += 1
        if sum(wc[j:]) < MIN_TAIL_WORDS:
            j = n
        out.append((i, j))
        if j >= n:
            break
        k, o = j, 0
        while k > i + 1 and o < OVERLAP_WORDS:
            k -= 1
            o += wc[k]
        i = k
    return out


def passages(r: dict) -> list[tuple[int, int, str]]:
    cues = r["cues"]
    return [
        (int(cues[i]["t"]), min(int(cues[j - 1]["e"] + 0.999), 65535),
         r["title"] + "\n" + " ".join(c["text"] for c in cues[i:j]))
        for i, j in chunk(cues)
    ]


def model_dir() -> str:
    d = os.path.join(os.environ.get("MM_MODEL_CACHE", os.path.expanduser("~/.cache/mm-embed")), REVISION)
    for name in (ONNX_FILE, "tokenizer.json"):
        path = os.path.join(d, name)
        if not os.path.exists(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            url = f"https://huggingface.co/{MODEL}/resolve/{REVISION}/{name}"
            print(f"embed: downloading {url}")
            urllib.request.urlretrieve(url, path + ".part")
            os.replace(path + ".part", path)
    return d


class Encoder:
    def __init__(self, threads: int):
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer

        d = model_dir()
        self.np = np
        self.tok = Tokenizer.from_file(os.path.join(d, "tokenizer.json"))
        self.tok.enable_truncation(MAX_TOKENS)
        self.tok.no_padding()
        so = ort.SessionOptions()
        so.intra_op_num_threads = threads
        so.enable_cpu_mem_arena = False
        self.sess = ort.InferenceSession(os.path.join(d, ONNX_FILE), so, providers=["CPUExecutionProvider"])
        self.inputs = {i.name for i in self.sess.get_inputs()}

    def __call__(self, texts: list[str], batch: int = 16):
        """CLS pooling + L2 norm, as transformers.js feature-extraction with pooling 'cls', normalize true."""
        np = self.np
        encs = self.tok.encode_batch(texts)
        out = np.zeros((len(texts), DIMS), dtype=np.float32)
        order = sorted(range(len(texts)), key=lambda k: len(encs[k].ids))
        for b in range(0, len(order), batch):
            idx = order[b:b + batch]
            width = max(len(encs[k].ids) for k in idx)
            ids = np.zeros((len(idx), width), dtype=np.int64)
            mask = np.zeros_like(ids)
            for row, k in enumerate(idx):
                e = encs[k].ids
                ids[row, :len(e)] = e
                mask[row, :len(e)] = 1
            feed = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in self.inputs:
                feed["token_type_ids"] = np.zeros_like(ids)
            cls = self.sess.run(None, feed)[0][:, 0]
            out[idx] = cls / np.linalg.norm(cls, axis=1, keepdims=True)
        return out


def cache_key(r: dict) -> str:
    payload = [CACHE_VERSION, MODEL, REVISION, ONNX_FILE, TARGET_WORDS, OVERLAP_WORDS, MIN_TAIL_WORDS, MAX_TOKENS,
               r["title"], [(c["t"], c["e"], c["text"]) for c in r["cues"]]]
    return hashlib.sha1(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()


def talk_row(r: dict) -> list:
    video = r["video"]
    provider = url = None
    if video and video.get("timestamps", True) and video.get("provider") in AT_TIME:
        provider, url = video["provider"], video_url(video)
    return [r["id"], r["title"], r["date"] or "", platform_name(r), r["series"], year(r), provider, url]


def build(build_dir: str, dist: str, threads: int) -> dict:
    import numpy as np

    with open(os.path.join(build_dir, "manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    cache_dir = os.path.join(build_dir, "embed-cache")
    os.makedirs(cache_dir, exist_ok=True)
    encoder = None
    talks, spans, vecs, used = [], [], [], set()
    embedded = cached = 0
    t_embed = 0.0
    for t in manifest["talks"]:
        if "empty" in t["flags"]:
            continue
        with open(os.path.join(build_dir, "records", t["id"] + ".json"), encoding="utf-8") as f:
            r = json.load(f)
        ps = passages(r)
        if not ps:
            continue
        key = cache_key(r)
        used.add(key + ".npy")
        path = os.path.join(cache_dir, key + ".npy")
        if os.path.exists(path):
            v = np.load(path)
            cached += 1
        else:
            encoder = encoder or Encoder(threads)
            t0 = time.time()
            v = encoder([p[2] for p in ps]).astype(np.float16)
            t_embed += time.time() - t0
            np.save(path + ".tmp.npy", v)
            os.replace(path + ".tmp.npy", path)
            embedded += 1
        assert v.shape == (len(ps), DIMS), (t["id"], v.shape)
        ti = len(talks)
        talks.append(talk_row(r))
        spans += [(ti, s, e) for s, e, _ in ps]
        vecs.append(v)
    for name in os.listdir(cache_dir):
        if name.endswith(".npy") and name not in used:
            os.remove(os.path.join(cache_dir, name))

    v = np.concatenate(vecs).astype(np.float32)
    sp = np.array(spans, dtype=np.uint16)
    bits = np.packbits(v > 0, axis=1)
    i8 = np.round(v / np.abs(v).max(axis=1, keepdims=True) * 127).astype(np.int8)
    files = {"chunks.bin": sp.T.copy().tobytes(), "bin.bin": bits.tobytes(), "i8.bin": i8.tobytes()}
    out = os.path.join(dist, "semantic")
    os.makedirs(out, exist_ok=True)
    h = hashlib.sha1()
    for name, data in files.items():
        h.update(data)
        with open(os.path.join(out, name), "wb") as f:
            f.write(data)
    meta = {"v": h.hexdigest()[:12], "model": MODEL, "revision": REVISION, "dims": DIMS, "n": len(spans),
            "query_prefix": QUERY_PREFIX, "talks": talks}
    with open(os.path.join(out, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, separators=(",", ":"))
    return {"talks": len(talks), "chunks": len(spans), "embedded_talks": embedded, "cached_talks": cached,
            "embed_s": round(t_embed, 1), "bytes": {k: len(d) for k, d in files.items()}}
