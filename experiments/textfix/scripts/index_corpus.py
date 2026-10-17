"""Corpus word counts + OOV list. Writes results/wordcounts.json, results/oov.tsv."""
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import RES, WORD_RE, in_lexicon, load_lexicon, parse_srt, srt_files, CORPUS  # noqa: E402

lex = load_lexicon()
counts = collections.Counter()
docs = collections.Counter()
total = 0
files = srt_files()
for p in files:
    seen = set()
    for cue in parse_srt(os.path.join(CORPUS, p)):
        for w in WORD_RE.findall(cue):
            lw = w.lower().strip("'")
            if lw.endswith("'s"):
                lw = lw[:-2]
            counts[lw] += 1
            seen.add(lw)
            total += 1
    for w in seen:
        docs[w] += 1
with open(os.path.join(RES, "wordcounts.json"), "w") as fh:
    json.dump({"total": total, "files": len(files), "counts": counts, "docs": docs}, fh)
oov = [(w, n, docs[w]) for w, n in counts.items() if len(w) >= 4 and not in_lexicon(w, lex)]
oov.sort(key=lambda x: -x[1])
with open(os.path.join(RES, "oov.tsv"), "w") as fh:
    fh.write("word\tcount\tdocs\n")
    for w, n, d in oov:
        fh.write(f"{w}\t{n}\t{d}\n")
print("files", len(files), "tokens", total, "types", len(counts), "oov types", len(oov))
for k in (1, 2, 3, 5, 10, 50):
    print(f"oov with count>={k}:", sum(1 for x in oov if x[1] >= k))
