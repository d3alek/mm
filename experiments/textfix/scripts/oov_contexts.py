"""For each corpus OOV word (len>=4) collect up to 3 short contexts (<=12 words) from distinct files.
Writes results/oov_contexts.jsonl. Corpus-wide, offline; does not look at the sample or gold."""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import CORPUS, RES, WORD_RE, parse_srt, srt_files, write_jsonl  # noqa: E402

MIN_COUNT = int(sys.argv[1]) if len(sys.argv) > 1 else 1
oov = {}
with open(os.path.join(RES, "oov.tsv")) as fh:
    next(fh)
    for line in fh:
        w, n, d = line.rstrip("\n").split("\t")
        if int(n) >= MIN_COUNT:
            oov[w] = int(n)
rng = random.Random(7)
ctx = {w: [] for w in oov}
files = srt_files()
rng.shuffle(files)
for p in files:
    text = " ".join(parse_srt(os.path.join(CORPUS, p)))
    toks = text.split()
    seen = set()
    for k, raw in enumerate(toks):
        m = WORD_RE.search(raw)
        if not m:
            continue
        w = m.group(0).lower().strip("'")
        if w.endswith("'s"):
            w = w[:-2]
        if w in ctx and w not in seen and len(ctx[w]) < 3:
            seen.add(w)
            ctx[w].append(" ".join(toks[max(0, k - 6): k + 6]))
rows = [{"word": w, "count": n, "ctx": ctx[w]} for w, n in sorted(oov.items(), key=lambda x: -x[1])]
write_jsonl(os.path.join(RES, "oov_contexts.jsonl"), rows)
print(len(rows), "words")
