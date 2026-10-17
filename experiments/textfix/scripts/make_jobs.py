"""Build job files for run_llm.py.
usage: make_jobs.py MODE MODEL_SHORT [TAG]
MODE: gold | fixB | mineC | reviewC | residD
MODEL_SHORT: haiku | sonnet | opus | fable
Writes results/jobs_<MODE>_<MODEL><TAG>.jsonl"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DOMAIN_HINT, RES, read_jsonl, write_jsonl  # noqa: E402

MODELS = {"haiku": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5", "opus": "claude-opus-5-5",
          "fable": "claude-fable-5-1"}
ROOT = os.path.dirname(RES)
P = lambda name: open(os.path.join(ROOT, "prompts", name)).read().replace("{DOMAIN_HINT}", DOMAIN_HINT)
mode, short = sys.argv[1], sys.argv[2]
tag = sys.argv[3] if len(sys.argv) > 3 else ""
model = MODELS[short]
sample = read_jsonl(os.path.join(RES, "sample.jsonl"))
jobs = []
if mode == "gold":
    sys_p = P("gold_system.txt")
    for s in sample:
        jobs.append({"id": s["id"], "model": model, "effort": "high", "system": sys_p,
                     "prompt": "<transcript>\n" + s["text"] + "\n</transcript>"})
elif mode == "fixB":
    sys_p = P("fix_system.txt")
    for s in sample:
        jobs.append({"id": s["id"], "model": model, "system": sys_p,
                     "prompt": "<transcript>\n" + s["text"] + "\n</transcript>"})
elif mode == "residD":
    # residual pass on glossary-fixed text of the chunks that still hold a suspicious token
    sys_p = P("fix_system.txt")
    inputs = "D_inputs_all.jsonl" if tag == "_all" else "D_inputs.jsonl"
    for r in read_jsonl(os.path.join(RES, inputs)):
        if r["triggered"]:
            jobs.append({"id": r["id"], "model": model, "system": sys_p,
                         "prompt": "<transcript>\n" + r["text"] + "\n</transcript>"})
elif mode == "mineC":
    sys_p = P("mine_system.txt")
    words = read_jsonl(os.path.join(RES, "oov_contexts.jsonl"))
    B = 80
    for i in range(0, len(words), B):
        batch = words[i:i + B]
        body = "\n".join(f"- {w['word']} (count {w['count']}): " + " | ".join(w["ctx"]) for w in batch)
        jobs.append({"id": f"m{i // B:03d}", "model": model, "system": sys_p,
                     "prompt": "<words>\n" + body + "\n</words>"})
elif mode == "reviewC":
    sys_p = P("review_system.txt")
    props = read_jsonl(os.path.join(RES, f"C_proposals_{short}.jsonl"))
    B = 60
    for i in range(0, len(props), B):
        batch = props[i:i + B]
        body = "\n".join(
            f"{j}. {p['from']} -> {p['to']}"
            + (f" [only when one of {p['context']} is within {p.get('window', 8)} words]" if p.get("context") else "")
            + f" (count {p['count']}): " + " | ".join(p["ctx"])
            for j, p in enumerate(batch, i))
        jobs.append({"id": f"r{i // B:03d}", "model": model, "system": sys_p,
                     "prompt": "<proposals>\n" + body + "\n</proposals>"})
out = os.path.join(RES, f"jobs_{mode}_{short}{tag}.jsonl")
write_jsonl(out, jobs)
print(out, len(jobs), "jobs; prompt chars", sum(len(j["prompt"]) + len(j["system"]) for j in jobs))
