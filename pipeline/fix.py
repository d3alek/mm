"""Text-fix stage: glossary (`pipeline/glossary.json`), then Opus edits (`pipeline/fixes/<id>.jsonl`).

Both run on the record's cues joined by single spaces, so a phrase split across two cues still matches.
A changed cue keeps its original text in `text_raw`.

Glossary entries are {from, to, [context], [window]}: longest first, word-bounded, case-insensitive,
first-letter case kept; an entry with `context` fires only when one of those words is within `window`
words. Same semantics the textfix bakeoff measured (experiments/textfix/scripts/common.py).

Fix lines are {"cue", "at", "from", "to", "chunk", "sha"}: `from` must sit exactly at offset `at` of cue
`cue` in the glossary-fixed text (it may run on into following cues); otherwise the edit is skipped.
Lines without `from` only mark a chunk as done for the opus pass. A first line {"chunk_words": N} records
the chunk size the opus pass used for the talk (no such line: 250).
"""

import json
import os
import re

DIR = os.path.dirname(__file__)
GLOSSARY = os.path.join(DIR, "glossary.json")
FIXES = os.path.join(DIR, "fixes")


def load(path: str = GLOSSARY) -> list[tuple]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        entries = json.load(f)
    entries = [
        (e["from"], e["to"], frozenset(c.lower() for c in e.get("context") or [] if c.strip()), int(e.get("window") or 8))
        for e in entries
    ]
    entries.sort(key=lambda e: -len(e[0]))
    return [
        (re.compile(r"(?<![A-Za-z0-9'])" + re.escape(f) + r"(?![A-Za-z0-9])", re.I), _key(f), t, ctx, win)
        for f, t, ctx, win in entries
    ]


def _key(f: str) -> frozenset:
    """Lowercase alphanumeric runs of `f`; any match holds each as a whole run (cheap prefilter)."""
    return frozenset(re.findall(r"[a-z0-9]+", f.lower()))


def _match_case(src: str, dst: str) -> str:
    if src.isupper() and len(src) > 1:
        return dst.upper()
    if src[:1].isupper():
        return dst[:1].upper() + dst[1:]
    return dst  # keep canonical capitalisation of proper nouns


def _ctx_ok(text: str, s: int, e: int, ctx: frozenset, win: int) -> bool:
    if not ctx:
        return True
    for w in text[:s].split()[-win:] + text[e:].split()[:win]:
        w = re.sub(r"[^a-z0-9'-]", "", w.lower())
        if w in ctx or w.rstrip("s") in ctx or any(len(c) > 3 and w.startswith(c) for c in ctx):
            return True
    return False


def glossary_spans(text: str, rules) -> list[tuple[int, int, str]]:
    taken, out = [], []
    words = set(re.findall(r"[a-z0-9]+", text.lower()))
    for rx, key, to, ctx, win in rules:
        if not key <= words:
            continue
        for m in rx.finditer(text):
            s, e = m.span()
            if any(s < te and ts < e for ts, te in taken) or not _ctx_ok(text, s, e, ctx, win):
                continue
            taken.append((s, e))
            out.append((s, e, _match_case(m.group(0), to)))
    return sorted(out)


def offsets(texts: list[str]) -> list[int]:
    """Start of each cue in " ".join(texts)."""
    starts, pos = [], 0
    for t in texts:
        starts.append(pos)
        pos += len(t) + 1
    return starts


def splice(texts: list[str], spans) -> list[str]:
    """Apply non-overlapping (start, end, replacement) spans on the joined text back onto the cues.

    A span crossing cue boundaries gives each cue as many replacement words as it held source words
    when the word counts agree ("Medical | Meeting" -> "Medical | Medium"), so timing stays put;
    otherwise the whole replacement goes to the last cue (so trailing punctuation stays attached) and the
    covered text leaves the others.
    """
    starts = offsets(texts)
    ends = [s + len(t) for s, t in zip(starts, texts)]
    per_cue: dict[int, list] = {}
    for s, e, rep in spans:
        cues = [i for i in range(len(texts)) if starts[i] < e and s < ends[i]]
        if not cues:
            continue
        parts = [(i, max(s, starts[i]) - starts[i], min(e, ends[i]) - starts[i]) for i in cues]
        words = [len(texts[i][a:b].split()) for i, a, b in parts]
        rep_words = rep.split()
        if len(cues) > 1 and sum(words) == len(rep_words) and all(words):
            reps, k = [], 0
            for n in words:
                reps.append(" ".join(rep_words[k : k + n]))
                k += n
        else:
            reps = [""] * (len(cues) - 1) + [rep]
        for (i, a, b), r in zip(parts, reps):
            per_cue.setdefault(i, []).append((a, b, r))
    out = list(texts)
    for i, edits in per_cue.items():
        t = texts[i]
        for a, b, r in sorted(edits, reverse=True):
            t = t[:a] + r + t[b:]
        out[i] = re.sub(r"\s+", " ", t).strip()
    return out


def load_fixes(rec_id: str, fixes_dir: str = FIXES) -> list[dict]:
    path = os.path.join(fixes_dir, rec_id + ".jsonl")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [d for d in map(json.loads, filter(str.strip, f)) if "from" in d]


def edit_spans(texts: list[str], edits: list[dict]) -> tuple[list[tuple[int, int, str]], int]:
    """Edits that still match exactly, as spans; plus the number skipped (moved source or overlap)."""
    joined, starts = " ".join(texts), offsets(texts)
    spans, skipped = [], 0
    for ed in sorted(edits, key=lambda d: (d["cue"], d["at"])):
        if not 0 <= ed["cue"] < len(texts):
            skipped += 1
            continue
        s = starts[ed["cue"]] + ed["at"]
        e = s + len(ed["from"])
        if joined[s:e] != ed["from"] or (spans and s < spans[-1][1]):
            skipped += 1
            continue
        spans.append((s, e, ed["to"]))
    return spans, skipped


def glossary_texts(cues: list[dict], rules) -> list[str]:
    """Glossary-fixed cue texts from the original (pre-fix) texts; what fix lines are keyed to."""
    texts = [c.get("text_raw", c["text"]) for c in cues]
    return splice(texts, glossary_spans(" ".join(texts), rules)) if rules else texts


def apply(rec: dict, rules, edits: list[dict] | None = None) -> tuple[dict, int]:
    """Fix rec in place. Returns (rec, skipped edit count)."""
    raw = [c["text"] for c in rec["cues"]]
    texts = glossary_texts(rec["cues"], rules)
    glossary_changed = texts != raw
    spans, skipped = edit_spans(texts, edits or [])
    texts = splice(texts, spans)
    for c, old, new in zip(rec["cues"], raw, texts):
        if new != old:
            c["text_raw"] = old
            c["text"] = new
    flags = set(rec["flags"])
    if glossary_changed:
        flags.add("glossary_fixed")
    if spans:
        flags.add("opus_fixed")
    rec["flags"] = sorted(flags)
    return rec, skipped
