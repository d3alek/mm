"""Arm D (cascade): glossary pass, then an LLM only on chunks that still hold a suspicious token.
usage: build_D.py inputs GLOSSARY_FILE   -> results/D_inputs.jsonl + results/D_trigger.json (corpus-wide rate)
Suspicious token = not in the English lexicon, >=4 letters, corpus count <= RARE_MAX, and not a
glossary target or domain-hint term (same OOV definition as the sampling)."""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (CORPUS, DOMAIN_HINT, RES, WORD_RE, apply_spans, compile_entries, in_lexicon,  # noqa: E402
                    load_lexicon, parse_srt, read_jsonl, spans_for_entries, srt_files, write_jsonl)

RARE_MAX = 20
step, gfile = sys.argv[1], sys.argv[2]
g = json.load(open(os.path.join(RES, gfile)))
comp = compile_entries(g)
lex = load_lexicon()
counts = json.load(open(os.path.join(RES, "wordcounts.json")))["counts"]
safe = {w.lower() for e in g for w in WORD_RE.findall(e["to"])} | \
       {w.lower() for w in re.findall(r"[A-Za-z]+", DOMAIN_HINT)}


def suspicious(text):
    out = []
    for w in WORD_RE.findall(text):
        lw = w.lower().strip("'")
        if lw.endswith("'s"):
            lw = lw[:-2]
        if len(lw) >= 4 and lw not in safe and counts.get(lw, 0) <= RARE_MAX and not in_lexicon(lw, lex):
            out.append(lw)
    return out


if step == "inputs":
    rows = []
    for s in read_jsonl(os.path.join(RES, "sample.jsonl")):
        fixed = apply_spans(s["text"], spans_for_entries(s["text"], comp))
        sus = suspicious(fixed)
        rows.append({"id": s["id"], "stratum": s["stratum"], "text": fixed, "triggered": bool(sus),
                     "suspicious": sus})
    write_jsonl(os.path.join(RES, "D_inputs.jsonl"), rows)
    # corpus-wide trigger rate on the same 250-word chunking
    n = trig = words = trig_words = 0
    for p in srt_files():
        cues = parse_srt(os.path.join(CORPUS, p))
        i = 0
        while i < len(cues):
            j, k = i, 0
            while j < len(cues) and k < 250:
                k += len(cues[j].split())
                j += 1
            t = " ".join(cues[i:j])
            fixed = apply_spans(t, spans_for_entries(t, comp))
            n += 1
            words += k
            if suspicious(fixed):
                trig += 1
                trig_words += k
            i = j
    st = {"glossary": gfile, "rare_max": RARE_MAX,
          "sample_triggered": sum(r["triggered"] for r in rows), "sample_chunks": len(rows),
          "sample_triggered_by_stratum": {k: sum(r["triggered"] for r in rows if r["stratum"] == k)
                                          for k in ("random", "variant", "rare")},
          "corpus_chunks": n, "corpus_triggered": trig, "corpus_words": words, "corpus_triggered_words": trig_words}
    json.dump(st, open(os.path.join(RES, "D_trigger.json"), "w"), indent=1)
    print(st)
