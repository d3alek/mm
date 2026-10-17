"""Windows of ~N words around each gold chunk, plus their claude -p jobs (arm E / production prompt).

usage: python3 -I build_windows.py N            -> results/windows_<N>.jsonl, results/jobs_<N>.jsonl
       python3 -I build_windows.py 250x         -> the 1000-word windows re-cut into production 250-word
                                                  chunks (same text, baseline edit rate per 1k words)

Each gold chunk gets a target position in its window: the chunk's start sits at u*(N - chunk words) words,
with u drawn from a seeded, balanced cycle of thirds, so first/middle/last thirds are equally common
(talk boundaries can push a chunk elsewhere; the real position is recorded). A gold chunk that already
lies fully inside an earlier window of the same talk reuses it. n021 is excluded (sung lyrics).
A window is a list of parts (cue ranges); each part is one CLI call. Normal windows have one part."""
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TF = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(TF, "scripts"))
sys.path.insert(0, HERE)
from common import CORPUS, DOMAIN_HINT, RES, parse_srt, read_jsonl, write_jsonl  # noqa: E402
from glossary import fixed_text  # noqa: E402

OUT = os.path.join(os.path.dirname(HERE), "results")
SEED = 20261016
EXCLUDE = {"n021"}
MODEL = "claude-opus-5-5"
SYSTEM = open(os.path.join(TF, "prompts", "fix_system.txt")).read().replace("{DOMAIN_HINT}", DOMAIN_HINT)
assert SYSTEM == open(os.path.join(TF, "..", "..", "pipeline", "fix_prompt.txt")).read(), "prompt drifted from production"


def wc(cues, a, b):
    return sum(len(c.split()) for c in cues[a:b])


def window(cues, ca, cb, n, u):
    """Cue range [a, b) of about n words with chunk [ca, cb) starting near u*(n - L) words in."""
    L = wc(cues, ca, cb)
    pre_target = u * max(n - L, 0)
    a, pre = ca, 0
    while a > 0 and pre < pre_target:
        a -= 1
        pre += len(cues[a].split())
    b, tot = cb, pre + L
    while b < len(cues) and tot < n:
        tot += len(cues[b].split())
        b += 1
    while a > 0 and tot < n:  # hit the talk end: fill from the front
        a -= 1
        tot += len(cues[a].split())
    return a, b


def prod_chunks(cues, a, b, n):
    """Production chunking (opus_pass.chunks): take cues until the part holds >= n words."""
    parts, i = [], a
    while i < b:
        j, k = i, 0
        while j < b and k < n:
            k += len(cues[j].split())
            j += 1
        parts.append([i, j])
        i = j
    return parts


def main(arg):
    sample = [s for s in read_jsonl(os.path.join(RES, "sample.jsonl")) if s["id"] not in EXCLUDE]
    if arg == "250x":
        src = read_jsonl(os.path.join(OUT, "windows_1000.jsonl"))
        wins = []
        for w in src:
            cues = parse_srt(os.path.join(CORPUS, w["file"]))
            wins.append(dict(w, id=w["id"].replace("w1000", "w250x"), size="250x",
                             parts=prod_chunks(cues, w["a"], w["b"], 250)))
    else:
        n = int(arg)
        rng = random.Random(f"{SEED}-{n}")
        order = sample[:]
        rng.shuffle(order)
        bins = []
        wins, by_file = [], {}
        for s in order:
            if not bins:
                bins = [0, 1, 2]
                rng.shuffle(bins)
            third = bins.pop()
            cues = parse_srt(os.path.join(CORPUS, s["file"]))
            ca, cb = s["cues"]
            host = next((w for w in by_file.get(s["file"], []) if w["a"] <= ca and cb <= w["b"]), None)
            if host is None:
                u = (third + rng.random()) / 3
                a, b = window(cues, ca, cb, n, u)
                host = {"id": f"w{n}_{len(wins):03d}", "size": n, "file": s["file"], "a": a, "b": b,
                        "words": wc(cues, a, b), "parts": [[a, b]], "golds": [], "target_third": third}
                wins.append(host)
                by_file.setdefault(s["file"], []).append(host)
            host["golds"].append({"id": s["id"], "ca": ca, "cb": cb})
        for w in wins:
            cues = parse_srt(os.path.join(CORPUS, w["file"]))
            for g in w["golds"]:
                pre = wc(cues, w["a"], g["ca"])
                g["pos"] = round((pre + wc(cues, g["ca"], g["cb"]) / 2) / w["words"], 3)
    jobs = []
    for w in wins:
        cues = parse_srt(os.path.join(CORPUS, w["file"]))
        for k, (a, b) in enumerate(w["parts"]):
            jobs.append({"id": f"{w['id']}_p{k:02d}", "model": MODEL, "system": SYSTEM,
                         "prompt": "<transcript>\n" + fixed_text(" ".join(cues[a:b])) + "\n</transcript>"})
    write_jsonl(os.path.join(OUT, f"windows_{arg}.jsonl"), wins)
    write_jsonl(os.path.join(OUT, f"jobs_{arg}.jsonl"), jobs)
    ws = sorted(w["words"] for w in wins)
    print(f"size {arg}: {len(wins)} windows, {len(jobs)} calls, golds {sum(len(w['golds']) for w in wins)}, "
          f"words min/median/max {ws[0]}/{ws[len(ws) // 2]}/{ws[-1]}")


if __name__ == "__main__":
    main(sys.argv[1])
