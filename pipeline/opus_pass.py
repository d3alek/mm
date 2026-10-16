"""Opus edit pass: every ~250-word chunk of the glossary-fixed records -> pipeline/fixes/<id>.jsonl.

Arm E of experiments/textfix: same prompt (fix_prompt.txt), same model, same chunking (cues are taken
until the chunk holds >= 250 words of original text). Reads build/records, so run `records` first.

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
CHUNK_WORDS = 250
CLI_OVERHEAD_TOKENS = 800  # harness tokens per `claude -p` call, measured in the bakeoff
with open(os.path.join(fix.DIR, "fix_prompt.txt"), encoding="utf-8") as _f:
    SYSTEM = _f.read()
PROMPT_SHA = hashlib.sha1(f"{MODEL}\0{SYSTEM}".encode()).hexdigest()

LIMIT_RE = re.compile(r"usage limit|limit reached|hit your limit|out of (?:extra )?usage", re.I)
RATE_RE = re.compile(r"rate.?limit|overloaded|\b429\b|\b529\b", re.I)


class UsageLimit(Exception):
    pass


def chunks(raw: list[str], fixed: list[str]) -> list[tuple[int, int, str, str]]:
    """(first cue, end cue, glossary-fixed text, sha); boundaries from original word counts."""
    out, i = [], 0
    while i < len(raw):
        j, n = i, 0
        while j < len(raw) and n < CHUNK_WORDS:
            n += len(raw[j].split())
            j += 1
        text = " ".join(fixed[i:j])
        out.append((i, j, text, hashlib.sha1(f"{PROMPT_SHA}\0{text}".encode()).hexdigest()[:16]))
        i = j
    return out


def parse_edits(s: str) -> list[dict] | None:
    m = re.search(r"\[.*\]", s.strip(), re.S)
    try:
        v = json.loads(m.group(0)) if m else None
    except json.JSONDecodeError:
        return None
    if not isinstance(v, list) or not all(isinstance(e, dict) and isinstance(e.get("from"), str) and isinstance(e.get("to"), str) for e in v):
        return None
    return v


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
    def __init__(self, build: str, fixes_dir: str, parallel: int, timeout: int, log_every: int):
        self.build, self.fixes_dir = build, fixes_dir
        self.opus_dir = os.path.join(build, "opus")
        self.parallel, self.timeout, self.log_every = parallel, timeout, log_every
        self.cwd = os.path.join(self.opus_dir, "cwd")  # empty dir: no project files reach the CLI
        os.makedirs(self.cwd, exist_ok=True)
        os.makedirs(fixes_dir, exist_ok=True)
        self.pause_until = 0.0
        self.lock = threading.Lock()

    # ---- state -------------------------------------------------------------------------------
    def plan(self, ids: list[str] | None) -> list[dict]:
        """Per record: current chunks, and the fixes file compacted to lines of current, still-exact chunks."""
        rules = fix.load()
        paths = sorted(glob.glob(os.path.join(self.build, "records", "*.json")))
        if ids:
            paths = [p for p in paths if os.path.basename(p)[:-5] in set(ids)]
        plans = []
        for p in paths:
            with open(p, encoding="utf-8") as f:
                rec = json.load(f)
            raw = [c.get("text_raw", c["text"]) for c in rec["cues"]]
            fixed = fix.glossary_texts(rec["cues"], rules)
            chs = chunks(raw, fixed)
            plans.append({"id": rec["id"], "platform": rec["platform"], "fixed": fixed, "chunks": chs})
            self._compact(rec["id"], fixed, {sha: k for k, (_, _, _, sha) in enumerate(chs)})
        return plans

    def _fixes_path(self, rid: str) -> str:
        return os.path.join(self.fixes_dir, rid + ".jsonl")

    def _read(self, rid: str) -> list[dict]:
        try:
            with open(self._fixes_path(rid), encoding="utf-8") as f:
                return [json.loads(l) for l in f if l.strip()]
        except FileNotFoundError:
            return []

    def _compact(self, rid: str, fixed: list[str], current: dict[str, int]):
        lines = self._read(rid)
        if not lines:
            return
        bad = {d["sha"] for d in lines if d["sha"] not in current}
        by_sha: dict[str, list] = {}
        for d in lines:
            by_sha.setdefault(d["sha"], []).append(d)
        for sha, ds in by_sha.items():
            edits = [d for d in ds if "from" in d]
            if edits and fix.edit_spans(fixed, edits)[1]:
                bad.add(sha)  # cue numbering moved under an unchanged chunk: redo it
        keep = [dict(d, chunk=current[d["sha"]]) for d in lines if d["sha"] not in bad]
        self._write(rid, keep)

    def _write(self, rid: str, lines: list[dict]):
        path = self._fixes_path(rid)
        if not lines:
            if os.path.exists(path):
                os.remove(path)
            return
        lines.sort(key=lambda d: (d["chunk"], d.get("cue", -1), d.get("at", -1)))
        data = "".join(json.dumps(d, ensure_ascii=False, sort_keys=True) + "\n" for d in lines)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(data)
        os.replace(tmp, path)

    def _append(self, rid: str, lines: list[dict]):
        with open(self._fixes_path(rid), "a", encoding="utf-8") as f:
            f.write("".join(json.dumps(d, ensure_ascii=False, sort_keys=True) + "\n" for d in lines))

    # ---- calling -----------------------------------------------------------------------------
    def _call(self, text: str) -> dict:
        cmd = ["claude", "-p", "--model", MODEL, "--tools", "", "--strict-mcp-config", "--no-session-persistence",
               "--setting-sources", "", "--output-format", "json", "--system-prompt", SYSTEM]
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
        res["wall_s"] = round(time.time() - t0, 2)
        return res

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
        done = {rid: {d["sha"] for d in self._read(rid)} for rid in (p["id"] for p in plans)}
        todo = [(p, k, c) for p in plans for k, c in enumerate(p["chunks"]) if c[3] not in done[p["id"]]]
        total = sum(len(p["chunks"]) for p in plans)
        pending = len(todo)
        todo = todo[:limit] if limit is not None else todo
        est_in = sum(len(SYSTEM) + len(c[2]) + 26 for _, _, c in todo) // 4 + CLI_OVERHEAD_TOKENS * len(todo)
        print(f"records {len(plans)}, chunks {total}, done {total - pending}, to run {len(todo)}, "
              f"est. input tokens {est_in:,} (~{est_in // max(len(todo), 1)}/chunk)")
        if dry_run or not todo:
            return
        stats = {"ok": 0, "cached": 0, "failed": 0, "edits": 0, "unfound": 0, "in": 0, "out": 0, "usd": 0.0}
        t0 = time.time()
        fail_path = os.path.join(self.opus_dir, "failures.jsonl")
        self.log(f"start: {len(todo)} chunks, parallel {self.parallel}")
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
                    self._append(p["id"], lines)
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
                lines = self._read(p["id"])
                if lines:
                    self._write(p["id"], lines)

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
         log_every: int = 10):
    r = Runner(build, fix.FIXES, parallel, timeout, log_every)
    r.run(r.plan(ids), limit, dry_run)
