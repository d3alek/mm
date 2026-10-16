"""Measured tokens/latency per LLM step and full-corpus extrapolation. -> results/cost.json"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import RES  # noqa: E402

PRICE = {"haiku": (0.10, 0.50), "sonnet": (2.0, 10.0), "opus": (4.0, 20.0)}  # $/M in, out
PAR = 6
corpus_chunks = json.load(open(os.path.join(RES, "chunkstats.json")))["chunks_total"]
trig = json.load(open(os.path.join(RES, "D_trigger.json"))) if os.path.exists(os.path.join(RES, "D_trigger.json")) else None


def measure(d):
    p = os.path.join(RES, d)
    if not os.path.isdir(p):
        return None
    n = tin = tout = wall = 0
    for f in os.listdir(p):
        r = json.load(open(os.path.join(p, f)))
        u = r["usage"]
        n += 1
        tin += u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0) + u.get("cache_read_input_tokens", 0)
        tout += u.get("output_tokens", 0)
        wall += r.get("wall_s", 0)
    return {"calls": n, "in": tin, "out": tout, "wall_s": round(wall, 1)}


def dollars(m, tin, tout):
    pi, po = PRICE[m]
    return round((tin * pi + tout * po) / 1e6, 2)


out = {}
for m in PRICE:
    row = {}
    b = measure(f"B_{m}_r1")
    if b and b["calls"]:
        k = corpus_chunks / b["calls"]
        row["B_per_chunk"] = {"measured": b, "corpus_usd": dollars(m, b["in"] * k, b["out"] * k),
                              "corpus_usd_batch": dollars(m, b["in"] * k, b["out"] * k) / 2,
                              "corpus_wall_h_at_6": round(b["wall_s"] * k / PAR / 3600, 1)}
    mine, rev = measure(f"C_mine_{m}"), measure(f"C_review_{m}")
    if mine:
        tin = mine["in"] + (rev["in"] if rev else 0)
        tout = mine["out"] + (rev["out"] if rev else 0)
        w = mine["wall_s"] + (rev["wall_s"] if rev else 0)
        row["C_mining_once"] = {"mine": mine, "review": rev, "corpus_usd": dollars(m, tin, tout),
                                "wall_h_at_6": round(w / PAR / 3600, 2)}
    d = measure(f"D_resid_{m}")
    if d and d["calls"] and trig:
        k = trig["corpus_triggered"] / d["calls"]
        row["D_residual"] = {"measured": d, "corpus_triggered_chunks": trig["corpus_triggered"],
                             "corpus_usd": dollars(m, d["in"] * k, d["out"] * k),
                             "corpus_usd_batch": dollars(m, d["in"] * k, d["out"] * k) / 2,
                             "corpus_wall_h_at_6": round(d["wall_s"] * k / PAR / 3600, 1)}
    out[m] = row
g = measure("gold_fable")
out["gold_fable"] = g
json.dump(out, open(os.path.join(RES, "cost.json"), "w"), indent=1)
print(json.dumps(out, indent=1))
