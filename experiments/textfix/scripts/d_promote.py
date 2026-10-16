"""How many of D's residual LLM edits could be promoted into the glossary, and how good are they.
Promotable = same from->to seen in >=2 chunks, or the target is a term (domain-hint word or out-of-lexicon).
Also scores B r2 runs for variance. -> results/d_promote.json"""
import collections
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import arms  # noqa: E402
from common import DOMAIN_HINT, RES, WORD_RE, in_lexicon, load_lexicon, norm  # noqa: E402

LEX = load_lexicon()
HINT = {w.lower() for w in re.findall(r"[A-Za-z]+", DOMAIN_HINT)}
sc = json.load(open(os.path.join(RES, "scores_gold_fable.json")))
out = {}
for m in ("haiku", "sonnet", "opus"):
    _, resid = arms.cascade_arm(f"D_resid_{m}")
    pairs = collections.Counter()
    for cid, eds in resid.items():
        for p in {(norm(e["from"]), norm(e["to"])) for e in eds if norm(e["from"]) != norm(e["to"])}:
            pairs[p] += 1
    edits = [(cid, e) for cid, eds in resid.items() for e in eds if norm(e["from"]) != norm(e["to"])]

    def is_term(e):
        return any(w.lower() in HINT or not in_lexicon(w.lower(), LEX) for w in WORD_RE.findall(e["to"]))

    prom = [(cid, e) for cid, e in edits if pairs[(norm(e["from"]), norm(e["to"]))] >= 2 or is_term(e)]
    # how many promotable edits were right per gold (TP rows of the D arm whose from matches)
    tp_from = {(r["chunk"], norm(r["from"])) for r in sc["rows"][f"D_{m}"] if r["kind"] == "TP"}
    prom_tp = sum(1 for cid, e in prom if any(k[0] == cid and (norm(e["from"]) in k[1] or k[1] in norm(e["from"]))
                                              for k in tp_from))
    out[m] = {"residual_edits": len(edits), "repeat_pairs_ge2": sum(1 for v in pairs.values() if v >= 2),
              "promotable": len(prom), "promotable_matching_gold_TP": prom_tp,
              "examples": [f"{e['from']} -> {e['to']}" for _, e in prom[:12]]}
json.dump(out, open(os.path.join(RES, "d_promote.json"), "w"), indent=1)
print(json.dumps(out, indent=1))
