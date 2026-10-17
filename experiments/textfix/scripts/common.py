"""Shared helpers for the textfix bakeoff. Pure stdlib; run with python3 -I."""
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
RES = os.path.join(ROOT, "results")
CORPUS = "/home/alek/code/mm/data/raw/extracted/TranscriptsSrt"

WORD_RE = re.compile(r"[A-Za-z][A-Za-z']*")

# Canonical MM vocabulary given to every arm as a domain hint (no variant mapping).
DOMAIN_HINT = (
    "Transcripts are talks by Anthony William, the 'Medical Medium' (brand: Medical Medium). "
    "Frequent proper names and terms: Vimergy (supplement brand), Epstein-Barr virus (EBV), "
    "methylcobalamin, adenosylcobalamin (vitamin B12 forms), monolaurin, L-lysine, Atlantic dulse, "
    "mullein, orgonite, spirulina, barley grass juice powder, cilantro, wild blueberries, celery juice, "
    "heavy metal detox, lemon balm, nettle leaf, cat's claw, eleuthero, schisandra, ashwagandha, "
    "hibiscus, licorice root, chaga, goldenseal, nascent iodine, zinc sulfate, glutathione, "
    "Streptococcus, shingles, HHV-6, cytomegalovirus, Brain Saver, Cleanse to Heal, Life-Changing Foods, "
    "Thyroid Healing, Liver Rescue, Celery Juice (book titles)."
)

SEED_GLOSSARY = [
    # (variant, canonical) ; word-boundary, case-insensitive match, case of first letter preserved
    ("vimrigy", "Vimergy"), ("vimrgy", "Vimergy"), ("vimmergy", "Vimergy"), ("vimpergy", "Vimergy"),
    ("vivergy", "Vimergy"), ("vimbergy", "Vimergy"), ("vimurgy", "Vimergy"),
    ("Medical Meeting", "Medical Medium"), ("Medical Median", "Medical Medium"),
    ("Epstein bar", "Epstein-Barr"),
    ("methicobalamin", "methylcobalamin"), ("adenosilcobalamin", "adenosylcobalamin"),
    ("monolating", "monolaurin"), ("lycine", "lysine"),
    ("dolce", "dulse"), ("dulce", "dulse"), ("mullen", "mullein"), ("organite", "orgonite"),
]


def parse_srt(path):
    """Return list of cue texts (single-line joined) in order."""
    with open(path, encoding="utf-8", errors="replace") as fh:
        raw = fh.read()
    cues = []
    for block in re.split(r"\n\s*\n", raw.replace("\r", "")):
        lines = [l for l in block.split("\n") if l.strip()]
        lines = [l for l in lines if not l.strip().isdigit() and "-->" not in l]
        if lines:
            cues.append(" ".join(l.strip() for l in lines))
    return cues


def srt_files():
    out = []
    for d, _, fs in os.walk(CORPUS):
        for f in fs:
            if f.endswith(".srt"):
                out.append(os.path.relpath(os.path.join(d, f), CORPUS))
    return sorted(out)


def load_lexicon():
    words = set()
    for name in ("en_US.dic", "en_GB.dic"):
        with open(os.path.join(DATA, name), encoding="utf-8", errors="replace") as fh:
            next(fh)
            for line in fh:
                w = line.split("/")[0].strip().lower()
                if w:
                    words.add(w)
    for name in ("english_wikipedia.txt", "us_tv_and_film.txt", "surnames.txt",
                 "female_names.txt", "male_names.txt"):
        with open(os.path.join(DATA, name), encoding="utf-8", errors="replace") as fh:
            for line in fh:
                w = line.strip().split()[0].lower() if line.strip() else ""
                if w:
                    words.add(w)
    return words


SUFFIXES = [("'s", ""), ("s'", ""), ("ies", "y"), ("ied", "y"), ("iest", "y"), ("ier", "y"),
            ("es", ""), ("s", ""), ("ed", ""), ("ed", "e"), ("d", ""), ("ing", ""), ("ing", "e"),
            ("ly", ""), ("er", ""), ("ers", ""), ("est", ""), ("ness", ""), ("ment", ""),
            ("ily", "y"), ("n't", ""), ("'re", ""), ("'ve", ""), ("'ll", ""), ("'d", ""), ("'m", "")]


def in_lexicon(w, lex):
    w = w.lower().strip("'")
    if w in lex:
        return True
    for suf, rep in SUFFIXES:
        if w.endswith(suf) and len(w) > len(suf) + 2:
            stem = w[: -len(suf)] + rep
            if stem in lex:
                return True
            if len(stem) > 2 and stem[-1] == stem[-2] and stem[:-1] in lex:  # running -> run
                return True
    return False


def compile_entries(entries):
    """entries: (from, to) tuples or dicts {from, to, [context], [window]}.
    Longest-first, word-boundary, case-insensitive. A context list makes the entry fire only when one
    of those words occurs within `window` words of the match."""
    norm_e = []
    for e in entries:
        if isinstance(e, dict):
            ctx = [c.lower() for c in (e.get("context") or []) if isinstance(c, str) and c.strip()]
            norm_e.append((e["from"], e["to"], frozenset(ctx), int(e.get("window") or 8)))
        else:
            norm_e.append((e[0], e[1], frozenset(), 0))
    norm_e.sort(key=lambda e: -len(e[0]))
    return [(re.compile(r"(?<![A-Za-z0-9'])" + re.escape(f) + r"(?![A-Za-z0-9])", re.I), f, t, ctx, win)
            for f, t, ctx, win in norm_e]


def _key(f):
    """Lowercase alphanumeric run that must appear in any text the entry matches (prefilter)."""
    runs = re.findall(r"[a-z0-9]+", f.lower())
    return max(runs, key=len) if runs else ""


def match_case(src, dst):
    if src.isupper() and len(src) > 1:
        return dst.upper()
    if src[:1].isupper():
        return dst[:1].upper() + dst[1:]
    # source lowercase: keep canonical capitalisation for proper nouns
    return dst


def _ctx_ok(text, s, e, ctx, win):
    if not ctx:
        return True
    near = text[:s].split()[-win:] + text[e:].split()[:win]
    for w in near:
        w = re.sub(r"[^a-z0-9'-]", "", w.lower())
        if w in ctx or w.rstrip("s") in ctx or any(len(c) > 3 and w.startswith(c) for c in ctx):
            return True
    return False


def spans_for_entries(text, compiled, log=None):
    """Return non-overlapping (start, end, replacement) for static entries, longest-first.
    If `log` is a list, append (from, conditional?) per fired entry."""
    taken = []
    out = []
    low = text.lower()
    for rx, f, t, ctx, win in compiled:
        if _key(f) not in low:
            continue
        for m in rx.finditer(text):
            s, e = m.span()
            if any(s < te and ts < e for ts, te in taken):
                continue
            if not _ctx_ok(text, s, e, ctx, win):
                continue
            taken.append((s, e))
            out.append((s, e, match_case(m.group(0), t)))
            if log is not None:
                log.append((s, e, f, bool(ctx)))
    return sorted(out)


def spans_from_diff(orig, final):
    """Word-level diff orig -> final as (start, end, replacement) spans on orig."""
    import difflib
    ot = [(m.start(), m.end(), m.group(0)) for m in re.finditer(r"\S+", orig)]
    ft = [m.group(0) for m in re.finditer(r"\S+", final)]
    sm = difflib.SequenceMatcher(None, [t[2] for t in ot], ft, autojunk=False)
    out = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            continue
        rep = " ".join(ft[j1:j2])
        if i1 < i2:
            out.append((ot[i1][0], ot[i2 - 1][1], rep))
        else:  # pure insertion: attach to the previous token
            if i1 > 0:
                out.append((ot[i1 - 1][0], ot[i1 - 1][1], ot[i1 - 1][2] + " " + rep))
            elif ot:
                out.append((ot[0][0], ot[0][1], rep + " " + ot[0][2]))
    return out


def spans_for_edits(text, edits):
    """LLM/gold edits [{from,to}] -> spans. Every word-bounded occurrence of `from` is replaced;
    exact-case match preferred, falls back to case-insensitive. Unfound edits are reported."""
    taken, out, unfound = [], [], []
    for ed in sorted(edits, key=lambda e: -len(e.get("from", ""))):
        f, t = ed.get("from", ""), ed.get("to", "")
        if not f or f == t:
            continue
        pat = r"(?<![A-Za-z0-9'])" + re.escape(f) + r"(?![A-Za-z0-9])"
        ms = list(re.finditer(pat, text))
        if not ms:
            ms = list(re.finditer(pat, text, re.I))
        if not ms:
            unfound.append(ed)
            continue
        for m in ms:
            s, e = m.span()
            if any(s < te and ts < e for ts, te in taken):
                continue
            taken.append((s, e))
            out.append((s, e, t, ed))
    return sorted(out, key=lambda x: x[0]), unfound


def apply_spans(text, spans):
    out, pos = [], 0
    for sp in sorted(spans, key=lambda x: x[0]):
        s, e, r = sp[0], sp[1], sp[2]
        out.append(text[pos:s])
        out.append(r)
        pos = e
    out.append(text[pos:])
    return "".join(out)


def norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def read_jsonl(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(l) for l in fh if l.strip()]


def write_jsonl(path, rows):
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def parse_json_list(s):
    """Extract the first JSON array from model output."""
    s = s.strip()
    m = re.search(r"\[.*\]", s, re.S)
    if not m:
        return None
    try:
        v = json.loads(m.group(0))
        return v if isinstance(v, list) else None
    except json.JSONDecodeError:
        return None
