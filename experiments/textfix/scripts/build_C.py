"""Arm C. usage: build_C.py proposals|glossary MODEL_SHORT
proposals: results/C_mine_<m>/ -> results/C_proposals_<m>.jsonl
glossary:  results/C_review_<m>/ -> results/glossary_C_<m>.json (accepted entries; seed entries are
           reviewed like mined ones) and results/glossary_Cnr_<m>.json (seed + all proposals, no review)."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import RES, SEED_GLOSSARY, norm, parse_json_list, read_jsonl, write_jsonl  # noqa: E402

step, m = sys.argv[1], sys.argv[2]
words = {w["word"]: w for w in read_jsonl(os.path.join(RES, "oov_contexts.jsonl"))}
SEED = [{"from": f, "to": t, "source": "seed"} for f, t in SEED_GLOSSARY]


def entry(p, to=None, source=""):
    e = {"from": p["from"], "to": to or p["to"], "source": source}
    if p.get("context"):
        e["context"], e["window"] = p["context"], p.get("window") or 8
    return e


if step == "proposals":
    props, seen, bad = [], set(), []
    d = os.path.join(RES, f"C_mine_{m}")
    for f in sorted(os.listdir(d)):
        eds = parse_json_list(json.load(open(os.path.join(d, f)))["result"])
        if eds is None:
            bad.append(f)
            continue
        for e in eds:
            if not isinstance(e, dict) or not e.get("from") or not e.get("to"):
                continue
            fr, to = str(e["from"]).strip(), str(e["to"]).strip()
            if norm(fr) == norm(to) or fr.lower() in seen:
                continue
            seen.add(fr.lower())
            src = words.get(fr.lower()) or next((words[k] for k in fr.lower().split() if k in words), None)
            ctx = e.get("context") if isinstance(e.get("context"), list) else None
            props.append({"from": fr, "to": to, "confidence": e.get("confidence"),
                          "context": ctx, "window": e.get("window") if ctx else None,
                          "count": src["count"] if src else 0, "ctx": src["ctx"] if src else []})
    # seed entries go through the same review, with corpus contexts (3 per entry, distinct files)
    import re
    from common import CORPUS, parse_srt, srt_files
    seed_ctx = {f: [] for f, _ in SEED_GLOSSARY}
    rx = {f: re.compile(r"(?<![A-Za-z])" + re.escape(f) + r"(?![A-Za-z])", re.I) for f, _ in SEED_GLOSSARY}
    seed_cnt = {f: 0 for f, _ in SEED_GLOSSARY}
    for path in srt_files():
        t = " ".join(parse_srt(os.path.join(CORPUS, path)))
        for f, r in rx.items():
            ms = list(r.finditer(t))
            seed_cnt[f] += len(ms)
            if ms and len(seed_ctx[f]) < 3:
                a, b = ms[0].span()
                seed_ctx[f].append(" ".join(t[:a].split()[-6:] + [t[a:b]] + t[b:].split()[:6]))
    seeds = [{"from": f, "to": t, "confidence": "seed", "context": None, "window": None,
              "count": seed_cnt[f], "ctx": seed_ctx[f], "seed": True} for f, t in SEED_GLOSSARY]
    props = seeds + [p for p in props if p["from"].lower() not in {f.lower() for f, _ in SEED_GLOSSARY}]
    write_jsonl(os.path.join(RES, f"C_proposals_{m}.jsonl"), props)
    print(m, "proposals", len(props), "conditional", sum(1 for p in props if p["context"]),
          "unparseable batches", bad)
elif step == "glossary":
    props = read_jsonl(os.path.join(RES, f"C_proposals_{m}.jsonl"))
    verdicts = {}
    d = os.path.join(RES, f"C_review_{m}")
    for f in sorted(os.listdir(d)):
        for v in parse_json_list(json.load(open(os.path.join(d, f)))["result"]) or []:
            if isinstance(v, dict) and isinstance(v.get("n"), int):
                verdicts[v["n"]] = v
    acc = [entry(p, verdicts[i].get("to"), f"{m}+review") for i, p in enumerate(props)
           if verdicts.get(i, {}).get("verdict") == "accept"]
    json.dump(acc, open(os.path.join(RES, f"glossary_C_{m}.json"), "w"), indent=1)
    json.dump([entry(p, source=m) for p in props],
              open(os.path.join(RES, f"glossary_Cnr_{m}.json"), "w"), indent=1)
    print(m, "reviewed", len(verdicts), "of", len(props), "accepted", len(acc),
          "conditional accepted", sum(1 for a in acc if a.get("context")))
