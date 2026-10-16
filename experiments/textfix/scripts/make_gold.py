"""usage: make_gold.py SRC_DIR NAME. Labels -> results/NAME.json; + results/adjudication.json -> results/gold_final.json.
adjudication.json: [{"chunk", "from", "to", "verdict": "sure"|"uncertain"|"drop", "note"}]
  sure/uncertain on a (chunk, from) pair present in gold sets its confidence (and target); absent -> added.
  drop removes that gold item."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import RES, parse_json_list, read_jsonl  # noqa: E402

SRC = sys.argv[1] if len(sys.argv) > 1 else "gold_opus_raw"
NAME = sys.argv[2] if len(sys.argv) > 2 else "gold_opus"
ids = [s["id"] for s in read_jsonl(os.path.join(RES, "sample.jsonl"))]
gold, bad = {}, []
for cid in ids:
    p = os.path.join(RES, SRC, cid + ".json")
    eds = parse_json_list(json.load(open(p))["result"]) if os.path.exists(p) else None
    if eds is None:
        bad.append(cid)
        eds = []
    gold[cid] = [{"from": e["from"], "to": e["to"], "reason": e.get("reason", ""),
                  "confidence": e.get("confidence", "uncertain")} for e in eds
                 if isinstance(e, dict) and e.get("from") and e.get("to") is not None]
json.dump(gold, open(os.path.join(RES, NAME + ".json"), "w"), indent=1)
print(NAME, "unparseable", bad, "items", sum(len(v) for v in gold.values()),
      "sure", sum(1 for v in gold.values() for e in v if e["confidence"] == "sure"))

adj_p = os.path.join(RES, "adjudication.json")
adj = json.load(open(adj_p)) if os.path.exists(adj_p) else []
final = json.loads(json.dumps(gold))
n = {"changed": 0, "added": 0, "dropped": 0}
for a in adj:
    items = final[a["chunk"]]
    hit = [e for e in items if e["from"].lower() == a["from"].lower()]
    if a["verdict"] == "drop":
        final[a["chunk"]] = [e for e in items if e not in hit]
        n["dropped"] += len(hit)
    elif hit:
        for e in hit:
            e["confidence"], e["to"], e["adjudicated"] = a["verdict"], a["to"], a.get("note", "")
        n["changed"] += 1
    else:
        items.append({"from": a["from"], "to": a["to"], "reason": a.get("note", ""),
                      "confidence": a["verdict"], "adjudicated": "added"})
        n["added"] += 1
json.dump(final, open(os.path.join(RES, NAME + "_final.json"), "w"), indent=1)
print("gold_final", n, "sure", sum(1 for v in final.values() for e in v if e["confidence"] == "sure"))
