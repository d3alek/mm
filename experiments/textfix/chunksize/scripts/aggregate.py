"""Pool reps per size and control position effects against the 250-word baseline on the SAME gold items.
usage: python3 -I aggregate.py   (after score_cs.py) -> results/aggregate.json, prints a markdown table

For each size, recall in a position third is compared with arm E's recall (both E runs pooled) on exactly
the gold items whose chunk sits in that third at that size; a raw per-third recall mixes position with
which chunks happen to land there. 'late gap' = R_last(size) - R_E(same items)."""
import collections
import json
import os
import statistics as st

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), "results")
rows = json.load(open(os.path.join(OUT, "rows.json")))
summ = json.load(open(os.path.join(OUT, "summary.json")))
BASE = [k for k in ("250", "250_r2") if k in rows]


def third(p):
    return "first" if p < 1 / 3 else "middle" if p < 2 / 3 else "last"


def pos_of(size):
    out = {}
    for line in open(os.path.join(OUT, f"windows_{size}.jsonl")):
        w = json.loads(line)
        for g in w["golds"]:
            out[g["id"]] = third(g["pos"])
    return out


def tally(runs, gold, ids=None):
    c = collections.Counter()
    for k in runs:
        for cid, rs in rows[k][gold].items():
            if ids is None or cid in ids:
                c.update(r["kind"] for r in rs)
    tp, fp, fn = c["TP"], c["FP_changed_correct"] + c["FP_wrong_target"], c["FN"]
    p, r = tp / (tp + fp) if tp + fp else 0, tp / (tp + fn) if tp + fn else 0
    return {"P": round(p, 3), "R": round(r, 3), "F1": round(2 * p * r / (p + r), 3) if tp else 0, "n": (tp + fn) // len(runs)}


agg = {}
sizes = sorted({k.rsplit("_", 1)[0] for k in rows if k not in BASE}, key=lambda s: (s.endswith("x"), int(s.rstrip("x"))))
agg["250"] = {g: tally(BASE, g) for g in ("adjudicated", "fable")}
agg["250"]["runs"] = len(BASE)
agg["250"]["F1_runs_adj"] = [summ[k]["adjudicated"]["F1"] for k in BASE]
for size in sizes:
    runs = [k for k in rows if k.rsplit("_", 1)[0] == size and k not in BASE]
    pos = pos_of(size)
    a = {g: tally(runs, g) for g in ("adjudicated", "fable")}
    a["runs"] = len(runs)
    a["F1_runs_adj"] = [summ[k]["adjudicated"]["F1"] for k in runs]
    a["F1_runs_fable"] = [summ[k]["fable"]["F1"] for k in runs]
    for t in ("first", "middle", "last"):
        ids = {c for c, p in pos.items() if p == t}
        a[f"R_{t}"] = tally(runs, "adjudicated", ids)["R"]
        a[f"R_{t}_E"] = tally(BASE, "adjudicated", ids)["R"]
        a[f"n_{t}"] = tally(runs, "adjudicated", ids)["n"]
    a["late_gap"] = round(a["R_last"] - a["R_last_E"], 3)
    r1 = summ[f"{size}_r1"]
    for k in ("mean_wall_s", "mean_usd", "mean_in", "mean_out", "mean_thinking", "mean_window_words",
              "llm_per_1k_inside", "llm_per_1k_outside", "unparseable", "not_end_turn", "straddling_edits"):
        a[k] = r1.get(k)
    a["jaccard_r1_r2"] = summ.get(f"{size}_r2", {}).get("jaccard_vs_r1")
    agg[size] = a
agg["250"]["jaccard_r1_r2"] = summ.get("250_r2", {}).get("jaccard_vs_r1")
for k in ("mean_wall_s", "mean_usd", "mean_in", "mean_out", "mean_thinking", "llm_per_1k_inside", "unparseable"):
    agg["250"][k] = summ["250"].get(k)
json.dump(agg, open(os.path.join(OUT, "aggregate.json"), "w"), indent=1)

print("| size | runs | F1 adj (pooled; per run) | F1 Fable (pooled) | R adj | late-third R vs E on same items | first / middle | LLM edits/1k outside chunk | s/call | in/out tok | $/call (r1) | J r1-r2 |")
print("|---|---|---|---|---|---|---|---|---|---|---|---|")
for size, a in agg.items():
    late = f"{a['R_last']} vs {a['R_last_E']} ({a['late_gap']:+}, n {a['n_last']})" if "R_last" in a else "–"
    fm = f"{a['R_first']} vs {a['R_first_E']} / {a['R_middle']} vs {a['R_middle_E']}" if "R_first" in a else "–"
    print(f"| {size} | {a['runs']} | {a['adjudicated']['F1']} ({'; '.join(map(str, a['F1_runs_adj']))}) | "
          f"{a['fable']['F1']} | {a['adjudicated']['R']} | {late} | {fm} | {a.get('llm_per_1k_outside', '–')} | "
          f"{a['mean_wall_s']} | {round(a['mean_in'])}/{round(a['mean_out'])} | {a['mean_usd']} | {a.get('jaccard_r1_r2')} |")
