"""Does fixing change search? Count corpus chunks matching canonical queries before/after
static arms (A, C) corpus-wide, and for all arms on the 150 sample chunks. -> results/search.json"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import arms  # noqa: E402
from common import CORPUS, RES, apply_spans, compile_entries, parse_srt, spans_for_entries, srt_files  # noqa: E402

Q = ["Vimergy", "Medical Medium", "Epstein-Barr", "methylcobalamin", "adenosylcobalamin", "monolaurin",
     "lysine", "dulse", "mullein", "spirulina", "streptococcus", "cytomegalovirus"]
QR = {q: re.compile(r"\b" + r"[\s-]?".join(re.escape(p) for p in re.split(r"[\s-]", q)) + r"\b", re.I) for q in Q}

chunks = []
for p in srt_files():
    cues = parse_srt(os.path.join(CORPUS, p))
    i = 0
    while i < len(cues):
        j, n = i, 0
        while j < len(cues) and n < 250:
            n += len(cues[j].split())
            j += 1
        chunks.append(" ".join(cues[i:j]))
        i = j

gl = {name: compile_entries([(e["from"], e["to"]) for e in json.load(open(os.path.join(RES, f)))])
      for name, f in (("A_static", "glossary_A.json"), ("C_opus", "glossary_C_opus.json"))}
res = {"corpus_chunks": len(chunks), "corpus": {q: {"before": 0} for q in Q}, "sample": {}}
for t in chunks:
    fixed = {k: apply_spans(t, spans_for_entries(t, c)) for k, c in gl.items()}
    for q, rx in QR.items():
        res["corpus"][q]["before"] += bool(rx.search(t))
        for k, ft in fixed.items():
            res["corpus"][q][k] = res["corpus"][q].get(k, 0) + bool(rx.search(ft))
armspans = {"A_static": arms.glossary_arm("glossary_A.json"), "C_opus": arms.glossary_arm("glossary_C_opus.json"),
            "B_haiku": arms.llm_arm("B_haiku_r1")[0], "B_opus": arms.llm_arm("B_opus_r1")[0],
            "D_opus": arms.cascade_arm("D_resid_opus")[0],
            "gold": {k: [(s, e, r) for s, e, r, c, _ in v if c == "sure"] for k, v in arms.gold("gold_fable.json").items()}}
for q, rx in QR.items():
    row = {"before": sum(bool(rx.search(t)) for t in arms.TEXT.values())}
    for name, sp in armspans.items():
        row[name] = sum(bool(rx.search(apply_spans(t, sp.get(cid, [])))) for cid, t in arms.TEXT.items())
    res["sample"][q] = row
json.dump(res, open(os.path.join(RES, "search.json"), "w"), indent=1)
print("corpus chunks", len(chunks))
for q in Q:
    print(f"{q:18}", res["corpus"][q], "| sample", res["sample"][q])
