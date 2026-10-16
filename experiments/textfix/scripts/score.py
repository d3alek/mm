"""Score every arm against a gold file. usage: score.py [gold_file=gold_fable.json]
-> results/scores_<gold>.json and a printed table."""
import collections
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import arms  # noqa: E402
from common import DOMAIN_HINT, RES, WORD_RE, apply_spans, in_lexicon, load_lexicon, norm  # noqa: E402

gold_file = sys.argv[1] if len(sys.argv) > 1 else "gold_fable.json"
G = arms.gold(gold_file)
TEXT = arms.TEXT
STRATUM = {s["id"]: s["stratum"] for s in arms.sample}
MODELS = ("haiku", "sonnet", "opus")
LEX = load_lexicon()
HINT = {w.lower() for w in re.findall(r"[A-Za-z]+", DOMAIN_HINT)}


def category(frm, to):
    """'term' = names/brands/jargon (glossary-addressable); 'common' = ordinary-word mishearing."""
    toks = [w.lower() for w in WORD_RE.findall(frm)]
    ttoks = [w.lower() for w in WORD_RE.findall(to)]
    if any(not in_lexicon(w, LEX) for w in toks) or any(w in HINT or not in_lexicon(w, LEX) for w in ttoks):
        return "term"
    if re.search(r"[A-Z]", to) and to.lower() != frm.lower() and any(w[:1].isupper() for w in WORD_RE.findall(to)[1:]):
        return "term"
    return "common"


def score_arm(spans_by_chunk, cond=None):
    rows = []
    for cid, text in TEXT.items():
        gs = G.get(cid, [])
        aspans = [sp for sp in spans_by_chunk.get(cid, []) if norm(text[sp[0]:sp[1]]) != norm(sp[2])]
        hit = set()
        for a in aspans:
            ov = [g for g in gs if a[0] < g[1] and g[0] < a[1]]
            rec = {"chunk": cid, "stratum": STRATUM[cid], "from": text[a[0]:a[1]], "to": a[2]}
            if cond is not None:
                rec["conditional"] = any(a[0] < e and s < a[1] for s, e in cond.get(cid, []))
            if not ov:
                rows.append(dict(rec, kind="FP_changed_correct"))
                continue
            sure = [g for g in ov if g[3] == "sure"]
            if not sure:
                rows.append(dict(rec, kind="neutral_uncertain"))
                continue
            lo = min([a[0]] + [g[0] for g in sure])
            hi = max([a[1]] + [g[1] for g in sure])
            a_in = [x for x in aspans if x[0] >= lo and x[1] <= hi]
            g_in = [g for g in gs if g[0] >= lo and g[1] <= hi]
            ra = apply_spans(text[lo:hi], [(x[0] - lo, x[1] - lo, x[2]) for x in a_in])
            rg = apply_spans(text[lo:hi], [(g[0] - lo, g[1] - lo, g[2]) for g in g_in])
            if norm(ra) == norm(rg):
                new = [g for g in sure if (g[0], g[1]) not in hit]
                for g in sure:
                    hit.add((g[0], g[1]))
                rows.append(dict(rec, kind="TP" if new else "TP_dup",
                                 cat=category(sure[0][4], sure[0][2])))
            else:
                rows.append(dict(rec, kind="FP_wrong_target", gold_to=sure[0][2]))
        for g in gs:
            if g[3] == "sure" and (g[0], g[1]) not in hit:
                rows.append({"chunk": cid, "stratum": STRATUM[cid], "from": g[4], "to": g[2], "kind": "FN",
                             "cat": category(g[4], g[2])})
    return rows


def summarize(rows, stratum=None):
    rs = [r for r in rows if stratum is None or r["stratum"] == stratum]
    c = collections.Counter(r["kind"] for r in rs)
    tp, fp, fn = c["TP"], c["FP_changed_correct"] + c["FP_wrong_target"], c["FN"]
    p = tp / (tp + fp) if tp + fp else float("nan")
    r = tp / (tp + fn) if tp + fn else float("nan")
    f = 2 * p * r / (p + r) if (tp and p + r) else 0.0
    out = {"TP": tp, "harm_changed_correct": c["FP_changed_correct"], "harm_wrong_target": c["FP_wrong_target"],
           "FN": fn, "neutral": c["neutral_uncertain"], "P": round(p, 3), "R": round(r, 3), "F1": round(f, 3)}
    for cat in ("term", "common"):
        t = sum(1 for x in rs if x["kind"] == "TP" and x.get("cat") == cat)
        n = sum(1 for x in rs if x["kind"] == "FN" and x.get("cat") == cat)
        out[f"R_{cat}"] = round(t / (t + n), 3) if t + n else None
    return out


def edit_set(spans_by_chunk):
    return {(cid, norm(TEXT[cid][s:e]), norm(r)) for cid, sp in spans_by_chunk.items() for s, e, r in sp
            if norm(TEXT[cid][s:e]) != norm(r)}


ARMS, COND, META, AGREE = {}, {}, {}, {}
ARMS["A0_seed"] = arms.glossary_arm("glossary_seed.json")
ARMS["A_static"] = arms.glossary_arm("glossary_A.json")
for m in MODELS:
    r1, r2 = f"B_{m}_r1", f"B_{m}_r2"
    if os.path.isdir(os.path.join(RES, r1)):
        ARMS[f"B_{m}"], META[r1] = arms.llm_arm(r1)
        if os.path.isdir(os.path.join(RES, r2)):
            sp2, META[r2] = arms.llm_arm(r2)
            a, b = edit_set(ARMS[f"B_{m}"]), edit_set(sp2)
            same_chunks = sum(1 for cid in TEXT if {x for x in a if x[0] == cid} == {x for x in b if x[0] == cid})
            AGREE[m] = {"jaccard_edits": round(len(a & b) / len(a | b), 3) if a | b else 1.0,
                        "chunks_identical": same_chunks, "r1_edits": len(a), "r2_edits": len(b)}
    for kind in ("C", "Cnr"):
        gf = f"glossary_{kind}_{m}.json"
        if os.path.exists(os.path.join(RES, gf)):
            COND[f"{kind}_{m}"] = {}
            ARMS[f"{kind}_{m}"] = arms.glossary_arm(gf, COND[f"{kind}_{m}"])
    if os.path.isdir(os.path.join(RES, f"D_resid_{m}")):
        ARMS[f"D_{m}"], _ = arms.cascade_arm(f"D_resid_{m}")
    if os.path.isdir(os.path.join(RES, f"E_all_{m}")):
        ARMS[f"E_{m}"], _ = arms.cascade_arm(f"E_all_{m}", "D_inputs_all.jsonl")
if gold_file != "gold_opus.json" and os.path.exists(os.path.join(RES, "gold_opus.json")):
    og = arms.gold("gold_opus.json")
    ARMS["opus_gold_as_arm"] = {cid: [(s, e, r) for s, e, r, c, _ in v if c == "sure"] for cid, v in og.items()}

out = {"gold": gold_file, "meta": META, "agreement": AGREE, "arms": {}, "rows": {}, "conditional_harm": {}}
gsure = sum(1 for v in G.values() for g in v if g[3] == "sure")
gunc = sum(1 for v in G.values() for g in v if g[3] != "sure")
print(f"gold {gold_file}: {gsure} sure spans, {gunc} uncertain")
print(f"{'arm':17} {'stratum':8} {'TP':>4} {'harmC':>5} {'harmW':>5} {'FN':>4} {'P':>6} {'R':>6} {'F1':>6} {'Rterm':>6} {'Rcomm':>6}")
for name, sp in ARMS.items():
    rows = score_arm(sp, COND.get(name))
    out["rows"][name] = rows
    out["arms"][name] = {st or "all": summarize(rows, st) for st in (None, "random", "variant", "rare")}
    if name in COND:
        out["conditional_harm"][name] = {
            "conditional_edits": sum(1 for r in rows if r.get("conditional")),
            "conditional_harmful": [r for r in rows if r.get("conditional") and r["kind"].startswith("FP")]}
    for st, s in out["arms"][name].items():
        if st != "all" and "-v" not in sys.argv:
            continue
        print(f"{name:17} {st:8} {s['TP']:4} {s['harm_changed_correct']:5} {s['harm_wrong_target']:5} "
              f"{s['FN']:4} {s['P']:6} {s['R']:6} {s['F1']:6} {str(s['R_term']):>6} {str(s['R_common']):>6}")
print("run-to-run:", AGREE)
print("conditional rules:", {k: (v["conditional_edits"], len(v["conditional_harmful"]))
                             for k, v in out["conditional_harm"].items()})
json.dump(out, open(os.path.join(RES, "scores_" + gold_file.replace(".json", "") + ".json"), "w"), indent=1)
