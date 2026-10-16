"""Opus edit pass: each talk's glossary-fixed text, in chunks of whole cues -> pipeline/fixes/<id>.jsonl.

Arm E of experiments/textfix (same prompt fix_prompt.txt, same model). Reads build/records, so run
`records` first. Chunk size is per talk and recorded as the fixes file's first line, {"chunk_words": N}:
- 250 (the first production run, arm E's rule: cues are taken until the chunk holds >= 250 words; a
  file without a header line was cut at this size).
- Any other N (default 8000, experiments/textfix/chunksize): the talk is cut at cue boundaries into
  ceil(words / N) chunks of about equal length, so a talk shorter than N is one chunk.
A talk whose file has every chunk done at its recorded size is kept as is. Any other talk is run whole at
the requested size: a partial file at another size is replaced on that talk's first new answer, so no
talk mixes sizes.

A chunk is identified by sha1(model, prompt, chunk text). It is done once a line carrying its sha is in
the fixes file; raw CLI responses are kept in build/opus/<id>/<sha>.json so a crash between the call and
the write costs nothing. Reruns skip done chunks; a changed record re-runs only the chunks whose text
changed (or whose stored edits no longer land exactly).
"""

import bisect
import concurrent.futures as cf
import datetime
import glob
import hashlib
import json
import os
import re
import subprocess
import threading
import time

from . import fix

MODEL = "claude-opus-5-5"
LEGACY_WORDS = 250  # size of the first production run; also what a headerless fixes file means
DEFAULT_WORDS = 8000
CLI_OVERHEAD_TOKENS = 800  # harness tokens per `claude -p` call, measured in the bakeoff
# Per-call fits from experiments/textfix/chunksize (r1 calls): seconds and list $ as a + b * words / 1000.
FIT_S, FIT_USD = (5.3, 1.0), (0.0097, 0.0146)
OUT_TOKENS_PER_KWORD = 122  # 974 output tokens per 8k-word call in the same test
MIN_MEM_KB = 3 * 1024 * 1024  # start a call only while MemAvailable >= 3 GB (14 GB laptop)
with open(os.path.join(fix.DIR, "fix_prompt.txt"), encoding="utf-8") as _f:
    SYSTEM = _f.read()
PROMPT_SHA = hashlib.sha1(f"{MODEL}\0{SYSTEM}".encode()).hexdigest()

LIMIT_RE = re.compile(r"usage limit|limit reached|hit your limit|out of (?:extra )?usage", re.I)
RATE_RE = re.compile(r"rate.?limit|overloaded|\b429\b|\b529\b", re.I)


class UsageLimit(Exception):
    pass


def cut(raw: list[str], words: int) -> list[tuple[int, int]]:
    """Cue ranges [i, j); boundaries from original word counts."""
    counts = [len(t.split()) for t in raw]
    if words == LEGACY_WORDS:  # arm E's rule, kept so the 250-word run's shas still match
        out, i = [], 0
        while i < len(raw):
            j, n = i, 0
            while j < len(raw) and n < words:
                n += counts[j]
                j += 1
            out.append((i, j))
            i = j
        return out
    k = max(1, -(-sum(counts) // words))
    target = sum(counts) / k
    out, i, acc = [], 0, 0
    for j, n in enumerate(counts, 1):
        acc += n
        if len(out) < k - 1 and acc >= target * (len(out) + 1):
            out.append((i, j))
            i = j
    if i < len(raw):
        out.append((i, len(raw)))
    return out


def chunks(raw: list[str], fixed: list[str], words: int = LEGACY_WORDS) -> list[tuple[int, int, str, str]]:
    """(first cue, end cue, glossary-fixed text, sha)."""
    out = []
    for i, j in cut(raw, words):
        text = " ".join(fixed[i:j])
        out.append((i, j, text, hashlib.sha1(f"{PROMPT_SHA}\0{text}".encode()).hexdigest()[:16]))
    return out


def _is_edits(v) -> bool:
    return isinstance(v, list) and all(isinstance(e, dict) and isinstance(e.get("from"), str) and isinstance(e.get("to"), str) for e in v)


def parse_edits(s: str) -> list[dict] | None:
    """The last JSON array of {from, to} in the answer (the model sometimes revises itself after a first one)."""
    dec, found, pos = json.JSONDecoder(), None, 0
    while (i := s.find("[", pos)) >= 0:
        try:
            v, end = dec.raw_decode(s, i)
        except json.JSONDecodeError:
            pos = i + 1
            continue
        if _is_edits(v):
            found, pos = v, end
        else:
            pos = i + 1
    return found


def mem_available_kb() -> int | None:
    try:
        with open("/proc/meminfo") as f:
            return next(int(l.split()[1]) for l in f if l.startswith("MemAvailable:"))
    except (OSError, StopIteration):
        return None


def chunk_spans(text: str, edits: list[dict]) -> tuple[list[tuple[int, int, str]], int]:
    """Bakeoff semantics: `from` replaces every word-bounded occurrence, exact case preferred, longest first."""
    taken, unfound = [], 0
    for ed in sorted(edits, key=lambda e: -len(e["from"])):
        f, t = ed["from"], ed["to"]
        if not f or f == t:
            continue
        pat = r"(?<![A-Za-z0-9'])" + re.escape(f) + r"(?![A-Za-z0-9])"
        ms = list(re.finditer(pat, text)) or list(re.finditer(pat, text, re.I))
        if not ms:
            unfound += 1
        for m in ms:
            if not any(m.start() < e and s < m.end() for s, e, _ in taken):
                taken.append((m.start(), m.end(), t))
    return sorted(taken), unfound


class Runner:
    def __init__(self, build: str, fixes_dir: str, parallel: int, timeout: int, log_every: int,
                 words: int = DEFAULT_WORDS):
        self.build, self.fixes_dir = build, fixes_dir
        self.opus_dir = os.path.join(build, "opus")
        self.parallel, self.timeout, self.log_every, self.words = parallel, timeout, log_every, words
        self.cwd = os.path.join(self.opus_dir, "cwd")  # empty dir: no project files reach the CLI
        os.makedirs(self.cwd, exist_ok=True)
        os.makedirs(fixes_dir, exist_ok=True)
        self.pause_until = 0.0
        self.lock = threading.Lock()
        self.mem_gate = threading.Lock()

    # ---- state -------------------------------------------------------------------------------
    def plan(self, ids: list[str] | None, write: bool = True) -> list[dict]:
        """Per record: its chunk size and chunks, and its fixes file compacted to current, still-exact chunks.

        status: "kept" (every chunk done at the file's size), "resume" (partly done at the requested size),
        "reset" (partly done at another size: run whole, old lines dropped on the talk's first new answer),
        "new" (no fixes file). write=False leaves the fixes files untouched (dry run).
        """
        rules = fix.load()
        paths = sorted(glob.glob(os.path.join(self.build, "records", "*.json")))
        if ids:
            paths = [p for p in paths if os.path.basename(p)[:-5] in set(ids)]
        plans = []
        for p in paths:
            with open(p, encoding="utf-8") as f:
                rec = json.load(f)
            rid = rec["id"]
            raw = [c.get("text_raw", c["text"]) for c in rec["cues"]]
            fixed = fix.glossary_texts(rec["cues"], rules)
            size, lines = self._read(rid)
            chs = chunks(raw, fixed, size)
            lines = self._compact(lines, fixed, chs)
            done = {d["sha"] for d in lines}
            if lines and all(c[3] in done for c in chs):
                status = "kept"
            elif lines and size == self.words:
                status = "resume"
            else:
                status, size, done = "reset" if lines else "new", self.words, set()
                chs = chunks(raw, fixed, size)
            if write and status in ("kept", "resume"):
                self._write(rid, size, lines)
            plans.append({"id": rid, "fixed": fixed, "words": size, "chunks": chs, "status": status, "done": done})
        return plans

    def _fixes_path(self, rid: str) -> str:
        return os.path.join(self.fixes_dir, rid + ".jsonl")

    def _read(self, rid: str) -> tuple[int, list[dict]]:
        """(chunk size, chunk lines); the size is the {"chunk_words": N} line, else the legacy 250."""
        try:
            with open(self._fixes_path(rid), encoding="utf-8") as f:
                rows = [json.loads(l) for l in f if l.strip()]
        except FileNotFoundError:
            return self.words, []
        size = next((d["chunk_words"] for d in rows if "chunk_words" in d), LEGACY_WORDS)
        return size, [d for d in rows if "sha" in d]

    def _compact(self, lines: list[dict], fixed: list[str], chs: list[tuple]) -> list[dict]:
        current = {sha: k for k, (_, _, _, sha) in enumerate(chs)}
        bad = {d["sha"] for d in lines if d["sha"] not in current}
        by_sha: dict[str, list] = {}
        for d in lines:
            by_sha.setdefault(d["sha"], []).append(d)
        for sha, ds in by_sha.items():
            edits = [d for d in ds if "from" in d]
            if edits and fix.edit_spans(fixed, edits)[1]:
                bad.add(sha)  # cue numbering moved under an unchanged chunk: redo it
        return [dict(d, chunk=current[d["sha"]]) for d in lines if d["sha"] not in bad]

    def _write(self, rid: str, size: int, lines: list[dict]):
        path = self._fixes_path(rid)
        if not lines:
            if os.path.exists(path):
                os.remove(path)
            return
        lines.sort(key=lambda d: (d["chunk"], d.get("cue", -1), d.get("at", -1)))
        rows = [{"chunk_words": size}] + lines
        data = "".join(json.dumps(d, ensure_ascii=False, sort_keys=True) + "\n" for d in rows)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(data)
        os.replace(tmp, path)

    def _append(self, plan: dict, lines: list[dict]):
        if plan["status"] in ("reset", "new"):  # the talk's first answer at this size replaces the file
            self._write(plan["id"], plan["words"], lines)
            plan["status"] = "resume"
            return
        with open(self._fixes_path(plan["id"]), "a", encoding="utf-8") as f:
            f.write("".join(json.dumps(d, ensure_ascii=False, sort_keys=True) + "\n" for d in lines))

    # ---- calling -----------------------------------------------------------------------------
    def _call(self, text: str) -> dict:
        cmd = ["claude", "-p", "--model", MODEL, "--tools", "", "--strict-mcp-config", "--no-session-persistence",
               "--setting-sources", "", "--output-format", "json", "--system-prompt", SYSTEM]
        self._wait_memory()
        t0 = time.time()
        p = subprocess.run(cmd, input="<transcript>\n" + text + "\n</transcript>", capture_output=True, text=True,
                           timeout=self.timeout, cwd=self.cwd)
        try:
            res = json.loads(p.stdout)
        except json.JSONDecodeError:
            res = {"is_error": True, "result": (p.stdout + p.stderr)[-500:]}
        msg = str(res.get("result", ""))
        if res.get("is_error") or p.returncode:
            if LIMIT_RE.search(msg) and not RATE_RE.search(msg):
                raise UsageLimit(msg)
            raise RuntimeError(msg[:300] or f"exit {p.returncode}")
        if res.get("stop_reason") == "max_tokens":
            raise RuntimeError("answer truncated at max_tokens")
        res["wall_s"] = round(time.time() - t0, 2)
        return res

    def _wait_memory(self):
        """One waiter at a time, with a short gap so the call started last shows up in MemAvailable."""
        with self.mem_gate:
            while (kb := mem_available_kb()) is not None and kb < MIN_MEM_KB:
                self.log(f"low memory ({kb // 1024} MB available), waiting")
                time.sleep(30)
            time.sleep(2)

    def _wait_pause(self):
        while (left := self.pause_until - time.time()) > 0:
            time.sleep(min(left, 60))

    def _pause(self, msg: str):
        until = reset_time(msg)
        with self.lock:
            if until > self.pause_until:
                self.pause_until = until
                self.log(f"usage limit: sleeping until {datetime.datetime.fromtimestamp(until):%H:%M} ({msg[:120]!r})")

    def work(self, rid: str, sha: str, text: str) -> tuple[dict | None, list | None, str]:
        """Raw response (cached or fresh) and parsed edits; edits None = failed, reason in the str."""
        raw_path = os.path.join(self.opus_dir, rid, sha + ".json")
        if os.path.exists(raw_path):
            with open(raw_path, encoding="utf-8") as f:
                res = json.load(f)
            return res, parse_edits(res.get("result", "")), "cached"
        errors, bad_parses, err = 0, 0, ""
        while True:
            self._wait_pause()
            try:
                res = self._call(text)
            except UsageLimit as e:
                self._pause(str(e))
                continue
            except (RuntimeError, subprocess.TimeoutExpired, OSError) as e:
                errors += 1
                err = repr(e)[:300]
                if errors >= 6:
                    return None, None, err
                time.sleep(min(10 * 2 ** errors, 600))
                continue
            edits = parse_edits(res.get("result", ""))
            if edits is None:
                bad_parses += 1
                if bad_parses < 2:
                    continue
                return res, None, "unparseable: " + str(res.get("result", ""))[:300]
            os.makedirs(os.path.dirname(raw_path), exist_ok=True)
            with open(raw_path + ".tmp", "w", encoding="utf-8") as f:
                json.dump(res, f, ensure_ascii=False)
            os.replace(raw_path + ".tmp", raw_path)
            return res, edits, "ok"

    def log(self, line: str):
        line = f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} {line}"
        print(line, flush=True)
        with open(os.path.join(self.opus_dir, "progress.log"), "a", encoding="utf-8") as f:
            f.write(line + "\n")

    # ---- run ---------------------------------------------------------------------------------
    def run(self, plans: list[dict], limit: int | None, dry_run: bool):
        todo = [(p, k, c) for p in plans for k, c in enumerate(p["chunks"]) if c[3] not in p["done"]]
        total = sum(len(p["chunks"]) for p in plans)
        pending = len(todo)
        todo = todo[:limit] if limit is not None else todo
        self.report(plans, todo, total, pending)
        if dry_run or not todo:
            return
        stats = {"ok": 0, "cached": 0, "failed": 0, "edits": 0, "unfound": 0, "in": 0, "out": 0, "usd": 0.0}
        t0 = time.time()
        fail_path = os.path.join(self.opus_dir, "failures.jsonl")
        self.log(f"start: {len(todo)} chunks at --words {self.words}, parallel {self.parallel}")
        ex = cf.ThreadPoolExecutor(self.parallel)
        try:
            futs = {ex.submit(self.work, p["id"], c[3], c[2]): (p, k, c) for p, k, c in todo}
            for n, fut in enumerate(cf.as_completed(futs), 1):
                p, k, (i, j, text, sha) = futs[fut]
                res, edits, status = fut.result()
                if edits is None:
                    stats["failed"] += 1
                    with open(fail_path, "a", encoding="utf-8") as f:
                        f.write(json.dumps({"id": p["id"], "chunk": k, "sha": sha, "error": status, "at": time.time()}) + "\n")
                else:
                    stats["ok" if status == "ok" else "cached"] += 1
                    lines = self._lines(p["fixed"], i, k, sha, text, edits, stats)
                    self._append(p, lines)
                    u = res.get("usage") or {}
                    stats["in"] += sum(u.get(x, 0) for x in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
                    stats["out"] += u.get("output_tokens", 0)
                    stats["usd"] += res.get("total_cost_usd", 0) if status == "ok" else 0
                if n % self.log_every == 0 or n == len(todo):
                    el = time.time() - t0
                    self.log(f"{n}/{len(todo)} chunks ({total} in scope) | failed {stats['failed']} | "
                             f"{el / n:.2f}s/chunk wall, ETA {fmt_s(el / n * (len(todo) - n))} | edits {stats['edits']} "
                             f"(unfound {stats['unfound']}) | tokens in {stats['in']:,} out {stats['out']:,} | list ${stats['usd']:.2f}")
        finally:
            ex.shutdown(wait=False, cancel_futures=True)
            for p in plans:  # tidy appended lines into sorted order
                if p["status"] == "resume":
                    self._write(p["id"], *self._read(p["id"]))

    def report(self, plans: list[dict], todo: list, total: int, pending: int):
        by: dict[str, int] = {}
        for p in plans:
            key = f"{p['status']}@{p['words']}"
            by[key] = by.get(key, 0) + 1
        words = [len(c[2].split()) for _, _, c in todo]
        est_in = sum(len(SYSTEM) + len(c[2]) + 26 for _, _, c in todo) // 4 + CLI_OVERHEAD_TOKENS * len(todo)
        est_out = sum(words) * OUT_TOKENS_PER_KWORD // 1000
        secs = sum(FIT_S[0] + FIT_S[1] * w / 1000 for w in words)
        usd = sum(FIT_USD[0] + FIT_USD[1] * w / 1000 for w in words)
        print(f"talks {len(plans)}: " + ", ".join(f"{k} {v}" for k, v in sorted(by.items()))
              + "  (kept = every chunk done at that size; reset = partial at another size, rerun whole)")
        print(f"chunks {total}, done {total - pending}, to run {len(todo)} calls at --words {self.words} "
              f"(words/call median {sorted(words)[len(words) // 2] if words else 0}, max {max(words, default=0)})")
        print(f"est. tokens in {est_in:,} out {est_out:,}; projected wall {fmt_s(secs / 3)} at 3-way, "
              f"{fmt_s(secs / self.parallel)} at --parallel {self.parallel}; list ~${usd:,.0f} (chunksize-test fits)")

    def _lines(self, fixed, first_cue, k, sha, text, edits, stats) -> list[dict]:
        starts = fix.offsets(fixed)
        base = starts[first_cue]
        joined = " ".join(fixed)
        spans, unfound = chunk_spans(text, edits)
        stats["edits"] += len(spans)
        stats["unfound"] += unfound
        lines = []
        for s, e, to in spans:
            cue = bisect.bisect_right(starts, base + s) - 1
            lines.append({"chunk": k, "sha": sha, "cue": cue, "at": base + s - starts[cue],
                          "from": joined[base + s : base + e], "to": to})
        return lines or [{"chunk": k, "sha": sha}]


def reset_time(msg: str) -> float:
    """When a usage limit lifts: an epoch in the message, else 'resets 3pm' / '3:30pm', else 30 min."""
    now = time.time()
    m = re.search(r"\b(1[6-9]\d{8})\b", msg)
    if m:
        return float(m[1]) + 60
    m = re.search(r"resets?\s+(?:at\s+)?(\d{1,2})(?::(\d\d))?\s*(am|pm)", msg, re.I)
    if m:
        h = int(m[1]) % 12 + (12 if m[3].lower() == "pm" else 0)
        t = datetime.datetime.now().replace(hour=h, minute=int(m[2] or 0), second=0, microsecond=0)
        if t.timestamp() <= now:
            t += datetime.timedelta(days=1)
        return t.timestamp() + 60
    return now + 1800


def fmt_s(s: float) -> str:
    return f"{int(s // 3600)}h{int(s % 3600 // 60):02d}m"


def main(build: str, ids: list[str] | None, limit: int | None, parallel: int, timeout: int, dry_run: bool,
         words: int = DEFAULT_WORDS, log_every: int = 10):
    r = Runner(build, fix.FIXES, parallel, timeout, log_every, words)
    r.run(r.plan(ids, write=not dry_run), limit, dry_run)
