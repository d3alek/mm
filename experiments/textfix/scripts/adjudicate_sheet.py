"""Adjudication sheet: edits where the Fable gold and >=1 contestant disagree.
usage: adjudicate_sheet.py [scores_file=scores_gold_fable.json] [cap=60] -> adjudicate.csv"""
import collections
import csv
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import RES, norm, read_jsonl  # noqa: E402

sc = json.load(open(os.path.join(RES, sys.argv[1] if len(sys.argv) > 1 else "scores_gold_fable.json")))
cap = int(sys.argv[2]) if len(sys.argv) > 2 else 60
TEXT = {s["id"]: s["text"] for s in read_jsonl(os.path.join(RES, "sample.jsonl"))}
CONTESTANTS = [a for a in sc["rows"] if a == "A_static" or re.match(r"^(B|C|D)_(haiku|sonnet|opus)$", a)]

items = collections.OrderedDict()
for arm in CONTESTANTS:
    for r in sc["rows"][arm]:
        if r["kind"] in ("TP", "TP_dup", "neutral_uncertain"):
            continue
        key = (r["chunk"], norm(r["from"])[:12])
        it = items.setdefault(key, {"chunk": r["chunk"], "from": r["from"], "to_gold": None, "arms": {}})
        if r["kind"] == "FN":
            it["to_gold"] = r["to"]
            it["arms"].setdefault(arm, "(no change)")
        elif r["kind"] == "FP_wrong_target":
            it["to_gold"] = r.get("gold_to")
            it["arms"][arm] = r["to"]
        else:
            it["arms"][arm] = r["to"]
for it in items.values():
    it["to_gold"] = it["to_gold"] or "(no change)"
ranked = sorted(items.values(), key=lambda x: (-len(x["arms"]), x["chunk"]))
# keep both directions: gold edits contestants missed/changed differently, and contestant edits gold rejected
no_change = [x for x in ranked if x["to_gold"] == "(no change)"][:cap // 4]
rows = sorted([x for x in ranked if x["to_gold"] != "(no change)"][:cap - len(no_change)] + no_change,
              key=lambda x: (-len(x["arms"]), x["chunk"]))
out_path = os.path.join(os.path.dirname(RES), "adjudicate.csv")
with open(out_path, "w", newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["chunk_id", "from", "to_gold", "to_contestants", "n_disagree", "context", "alek"])
    for it in rows:
        t = TEXT[it["chunk"]]
        m = re.search(re.escape(it["from"]), t, re.I)
        if m:
            left = t[:m.start()].split()[-5:]
            right = t[m.end():].split()[:5]
            mid = t[m.start():m.end()].split()
            ctx = " ".join(left + ["[" + " ".join(mid) + "]"] + right)
            ctx = " ".join(ctx.split()[:15])
        else:
            ctx = ""
        by_to = collections.defaultdict(list)
        for a, to in it["arms"].items():
            by_to[to].append(a)
        tc = "; ".join(f"{to} <= {','.join(sorted(a))}" for to, a in by_to.items())
        w.writerow([it["chunk"], it["from"], it["to_gold"], tc, len(it["arms"]), ctx, ""])
print("disagreements", len(ranked), "written", len(rows), "->", out_path)
