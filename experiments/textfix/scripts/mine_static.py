"""Arm A glossary: seed list + entries mined purely mechanically (frequency + edit distance).
Frozen before any gold labels exist. Writes results/glossary_A.json."""
import difflib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DOMAIN_HINT, RES, SEED_GLOSSARY, load_lexicon, in_lexicon  # noqa: E402

wc = json.load(open(os.path.join(RES, "wordcounts.json")))
counts = wc["counts"]
lex = load_lexicon()

hint_terms = {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z]{4,}", DOMAIN_HINT)}
# canonical vocabulary = domain jargon only: domain-hint terms + frequent (>=50) out-of-lexicon words.
# Common English words are NOT targets: near-misses of them are mostly real words (moringa ~ morning).
K = {w for w, n in counts.items() if n >= 50 and len(w) >= 5 and not in_lexicon(w, lex)} | hint_terms
Kl = sorted(K)
by_first = {}
for k in Kl:
    by_first.setdefault(k[0], []).append(k)

seed_from = {f.lower() for f, _ in SEED_GLOSSARY}
mined = []
for w, n in counts.items():
    if len(w) < 5 or w in hint_terms or w in seed_from or in_lexicon(w, lex) or "'" in w:
        continue
    cands = [k for k in by_first.get(w[0], []) if k != w]
    scored = sorted(((difflib.SequenceMatcher(None, w, k).ratio(), k) for k in cands), reverse=True)[:2]
    if not scored:
        continue
    r, k = scored[0]
    if r < 0.8:
        continue
    if len(scored) > 1 and scored[1][0] >= r - 0.02:
        continue  # ambiguous nearest neighbour
    if counts.get(k, 0) < 10 * n:
        continue
    if w.endswith("s") and w[:-1] == k or k.endswith("s") and k[:-1] == w:
        continue  # plural variants are not errors
    mined.append({"from": w, "to": k, "count": n, "to_count": counts.get(k, 0), "ratio": round(r, 3)})
# resolve chains (vimorgi -> vimrigi -> vimergy) and cycles
src = {m["from"]: m["to"] for m in mined}
for f, t in SEED_GLOSSARY:
    src.setdefault(f.lower(), t.lower())
for m in mined:
    t, hops = m["to"], 0
    while t in src and hops < 5:
        t, hops = src[t], hops + 1
    m["to"] = t
mined = [m for m in mined if m["to"] != m["from"]]
mined.sort(key=lambda x: -x["count"])
glossary = [{"from": f, "to": t, "source": "seed"} for f, t in SEED_GLOSSARY] + \
           [dict(m, source="mined") for m in mined]
json.dump(glossary, open(os.path.join(RES, "glossary_A.json"), "w"), indent=1)
print("seed", len(SEED_GLOSSARY), "mined", len(mined))
for m in mined[:60]:
    print(f"{m['from']}->{m['to']} ({m['count']}/{m['to_count']} r={m['ratio']})", end="; ")
