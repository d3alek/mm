"""Adjudicated gold = Fable gold + Alek's verdicts (verdicts_alek.json on adjudicate.csv rows).
usage: python3 -I adjudicated_gold.py -> results/gold_adjudicated.json

Row id is <chunk_id>-<csv row index>. Verdicts:
  gold   -> keep the gold item (nothing changes; rows where gold said "no change" stay unedited)
  model  -> the contestant edit in that row is right: it becomes a sure gold item (replacing a gold item on
            the same `from`, if any). Every model row here names exactly one contestant target.
  custom -> the gold item's `to` becomes Alek's text. One interpretation call: v044's custom text drops the
            trailing "'s" that the original span carries ("grapefruit's" = "grapefruit is"), so the
            possessive is kept; the correction is Oregon -> oregano.
Three csv rows carry no verdict (18, 19, 36); their gold items stay as Fable labelled them."""
import csv
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
TF = os.path.dirname(os.path.dirname(HERE))
OUT = os.path.join(os.path.dirname(HERE), "results")

gold = json.load(open(os.path.join(TF, "results", "gold_fable_final.json")))
rows = list(csv.DictReader(open(os.path.join(TF, "adjudicate.csv"))))
verdicts = json.load(open(os.path.join(TF, "verdicts_alek.json")))["verdicts"]
log = []
for rid, v in verdicts.items():
    cid, i = rid.rsplit("-", 1)
    r = rows[int(i)]
    assert r["chunk_id"] == cid, rid
    items = gold.setdefault(cid, [])
    same = [e for e in items if e["from"] == r["from"]]
    if v == "gold":
        continue
    if v == "model":
        tos = {t.split(" <= ")[0] for t in r["to_contestants"].split("; ")} - {"(no change)"}
        assert len(tos) == 1, (rid, tos)
        to = tos.pop()
    else:
        to = v["custom"]
        if r["from"].endswith("'s") and not to.endswith("'s"):
            to += "'s"
    for e in same:
        items.remove(e)
    items.append({"from": r["from"], "to": to, "reason": f"adjudicated {rid}: {v if isinstance(v, str) else 'custom'}",
                  "confidence": "sure"})
    log.append((rid, "replaced" if same else "added"))
json.dump(gold, open(os.path.join(OUT, "gold_adjudicated.json"), "w"), indent=1, ensure_ascii=False)
print(len(log), "changes:", log)
