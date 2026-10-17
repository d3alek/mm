"""Render results/*.json + adjudicate.csv into report.html (the published bakeoff page)."""
import csv
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXCLUDED_CHUNKS = {"n021"}  # sung song lyrics; not transcript speech

ARMS = [
    ("A_static", "A", "Static glossary", "—"),
    ("B_haiku", "B", "Model fixes every chunk", "Haiku"),
    ("B_sonnet", "B", "Model fixes every chunk", "Sonnet"),
    ("B_opus", "B", "Model fixes every chunk", "Opus"),
    ("C_haiku", "C", "Model writes glossary, script applies", "Haiku"),
    ("C_sonnet", "C", "Model writes glossary, script applies", "Sonnet"),
    ("C_opus", "C", "Model writes glossary, script applies", "Opus"),
    ("D_haiku", "D", "Opus glossary + model on flagged 11%", "Haiku"),
    ("D_sonnet", "D", "Opus glossary + model on flagged 11%", "Sonnet"),
    ("D_opus", "D", "Opus glossary + model on flagged 11%", "Opus"),
    ("E_opus", "E", "Opus glossary + Opus on every chunk", "Opus"),
]


def clean(x):
    return None if isinstance(x, float) and math.isnan(x) else x


def main():
    scores = json.load(open(os.path.join(HERE, "results/scores_gold_fable.json")))
    cost = json.load(open(os.path.join(HERE, "results/cost.json")))
    arms = []
    for key, letter, label, model in ARMS:
        a = scores["arms"][key]
        arms.append({
            "key": key, "letter": letter, "label": label, "model": model,
            "all": {k: clean(v) for k, v in a["all"].items()},
            "random_R": clean(a["random"]["R"]),
        })
    rows = []
    with open(os.path.join(HERE, "adjudicate.csv"), newline="", encoding="utf-8") as f:
        for i, r in enumerate(csv.DictReader(f)):
            if r["chunk_id"] in EXCLUDED_CHUNKS:
                continue
            rows.append({"id": f"{r['chunk_id']}-{i}", "from": r["from"], "gold": r["to_gold"],
                         "contestants": r["to_contestants"], "n": int(r["n_disagree"]), "context": r["context"]})
    data = {"arms": arms, "cost": cost, "agreement": scores["agreement"], "adjudicate": rows}
    template = open(os.path.join(HERE, "report.template.html"), encoding="utf-8").read()
    out = template.replace("/*DATA*/null", json.dumps(data, ensure_ascii=False))
    open(os.path.join(HERE, "report.html"), "w", encoding="utf-8").write(out)
    print(f"report.html: {len(arms)} arms, {len(rows)} adjudication rows", file=sys.stderr)


if __name__ == "__main__":
    main()
