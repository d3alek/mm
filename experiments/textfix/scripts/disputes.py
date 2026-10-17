"""List every disagreement between gold and any arm for adjudication.
usage: disputes.py scores_file -> results/disputes.jsonl"""
import collections
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import RES, read_jsonl, write_jsonl  # noqa: E402

sc = json.load(open(os.path.join(RES, sys.argv[1])))
TEXT = {s["id"]: s["text"] for s in read_jsonl(os.path.join(RES, "sample.jsonl"))}
agg = collections.OrderedDict()
for arm, rows in sc["rows"].items():
    for r in rows:
        if r["kind"] in ("TP", "TP_dup"):
            continue
        key = (r["chunk"], r["from"].lower(), r["to"].lower(), r["kind"] if r["kind"] == "FN" else "arm")
        d = agg.setdefault(key, {"chunk": r["chunk"], "from": r["from"], "to": r["to"],
                                 "kind": r["kind"], "gold_to": r.get("gold_to"), "arms": []})
        d["arms"].append(arm)
out = []
for d in agg.values():
    t = TEXT[d["chunk"]]
    m = re.search(re.escape(d["from"]), t, re.I)
    if m:
        s, e = m.span()
        left = " ".join(t[:s].split()[-8:])
        right = " ".join(t[e:].split()[:8])
        d["ctx"] = f"{left} [[{t[s:e]}]] {right}"
    out.append(d)
write_jsonl(os.path.join(RES, "disputes.jsonl"), out)
print(len(out), "disputes;", collections.Counter(d["kind"] for d in out))
