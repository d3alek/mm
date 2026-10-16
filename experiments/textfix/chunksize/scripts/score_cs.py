"""Score every chunk-size run against both golds; write results/summary.json and results/table.md.
usage: python3 -I score_cs.py

Per window: glossary_C_opus on each part's original text (as arm E), Opus edits applied with the bakeoff /
production semantics (`from` replaced at every word-bounded occurrence, longest first), then a word diff of
original window vs final window. Diff spans that lie inside a gold chunk are shifted onto the chunk text and
scored with score.py's matching rules (copied below, unchanged). Spans that straddle a chunk edge are counted.
LLM-only edits outside the gold chunks give edits per 1,000 words. The 250-word baseline is arm E itself
(results/E_all_opus), scored by the same code. n021 (sung lyrics) is excluded everywhere."""
import collections
import glob
import json
import os
import random
import re
import statistics as st
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TF = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(TF, "scripts"))
sys.path.insert(0, HERE)
from common import (CORPUS, DOMAIN_HINT, RES, WORD_RE, apply_spans, in_lexicon, load_lexicon, norm,  # noqa: E402
                    parse_srt, read_jsonl, spans_for_edits, spans_from_diff)
from glossary import glossary_spans  # noqa: E402

OUT = os.path.join(os.path.dirname(HERE), "results")
EXCLUDE = {"n021"}
SAMPLE = [s for s in read_jsonl(os.path.join(RES, "sample.jsonl")) if s["id"] not in EXCLUDE]
TEXT = {s["id"]: s["text"] for s in SAMPLE}
STRATUM = {s["id"]: s["stratum"] for s in SAMPLE}
LEX = load_lexicon()
HINT = {w.lower() for w in re.findall(r"[A-Za-z]+", DOMAIN_HINT)}
CUES = {}


def cues_of(f):
    if f not in CUES:
        CUES[f] = parse_srt(os.path.join(CORPUS, f))
    return CUES[f]


def load_gold(path):
    g = json.load(open(path))
    out = {}
    for cid in TEXT:
        sp, _ = spans_for_edits(TEXT[cid], g.get(cid, []))
        out[cid] = [(s, e, r, ed.get("confidence", "sure"), ed["from"]) for s, e, r, ed in sp]
    return out


GOLDS = {"adjudicated": load_gold(os.path.join(OUT, "gold_adjudicated.json")),
         "fable": load_gold(os.path.join(RES, "gold_fable_final.json"))}


# ---- score.py matching rules (copied verbatim, parametrised by gold) ----------------------------------
def category(frm, to):
    toks = [w.lower() for w in WORD_RE.findall(frm)]
    ttoks = [w.lower() for w in WORD_RE.findall(to)]
    if any(not in_lexicon(w, LEX) for w in toks) or any(w in HINT or not in_lexicon(w, LEX) for w in ttoks):
        return "term"
    if re.search(r"[A-Z]", to) and to.lower() != frm.lower() and any(w[:1].isupper() for w in WORD_RE.findall(to)[1:]):
        return "term"
    return "common"


def score_chunk(cid, spans, G):
    text, gs = TEXT[cid], G.get(cid, [])
    rows = []
    aspans = [sp for sp in spans if norm(text[sp[0]:sp[1]]) != norm(sp[2])]
    hit = set()
    for a in aspans:
        ov = [g for g in gs if a[0] < g[1] and g[0] < a[1]]
        rec = {"chunk": cid, "from": text[a[0]:a[1]], "to": a[2]}
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
            rows.append(dict(rec, kind="TP" if new else "TP_dup", cat=category(sure[0][4], sure[0][2])))
        else:
            rows.append(dict(rec, kind="FP_wrong_target", gold_to=sure[0][2]))
    for g in gs:
        if g[3] == "sure" and (g[0], g[1]) not in hit:
            rows.append({"chunk": cid, "from": g[4], "to": g[2], "kind": "FN", "cat": category(g[4], g[2])})
    return rows


def prf(rows):
    c = collections.Counter(r["kind"] for r in rows)
    tp, fp, fn = c["TP"], c["FP_changed_correct"] + c["FP_wrong_target"], c["FN"]
    p = tp / (tp + fp) if tp + fp else float("nan")
    r = tp / (tp + fn) if tp + fn else float("nan")
    f = 2 * p * r / (p + r) if tp else 0.0
    return {"TP": tp, "harmC": c["FP_changed_correct"], "harmW": c["FP_wrong_target"], "FN": fn,
            "P": round(p, 3), "R": round(r, 3), "F1": round(f, 3)}


# ---- model output ------------------------------------------------------------------------------------
def parse_edits(s):
    """pipeline/opus_pass.parse_edits: None when the answer is not a list of {from: str, to: str}."""
    m = re.search(r"\[.*\]", s.strip(), re.S)
    try:
        v = json.loads(m.group(0)) if m else None
    except json.JSONDecodeError:
        return None
    if not isinstance(v, list) or not all(isinstance(e, dict) and isinstance(e.get("from"), str)
                                          and isinstance(e.get("to"), str) for e in v):
        return None
    return v


def shift(p, spans):
    """Map an original-text offset (outside any span) to the text after `spans` are applied."""
    return p + sum(len(r) - (e - s) for s, e, r in spans if e <= p)


def run_window(w, outdir):
    """-> per-gold-chunk spans, LLM edit counts inside/outside, call metadata. None if any part is missing."""
    cues = cues_of(w["file"])
    origs, finals, calls, llm_pos, fixed_off, orig_off = [], [], [], [], 0, 0
    gl_all = []
    for k, (a, b) in enumerate(w["parts"]):
        p = os.path.join(outdir, f"{w['id']}_p{k:02d}.json")
        if not os.path.exists(p):
            return None
        res = json.load(open(p))
        orig = " ".join(cues[a:b])
        gl = glossary_spans(orig)
        fixed = apply_spans(orig, gl)
        eds = parse_edits(str(res.get("result", "")))
        u = res.get("usage") or {}
        calls.append({"wall_s": res["wall_s"], "usd": res.get("total_cost_usd", 0), "words": len(orig.split()),
                      "in": sum(u.get(x, 0) for x in ("input_tokens", "cache_creation_input_tokens",
                                                       "cache_read_input_tokens")),
                      "out": u.get("output_tokens", 0), "thinking": (u.get("output_tokens_details") or {}).get("thinking_tokens", 0),
                      "stop": res.get("stop_reason"), "parsed": eds is not None})
        sp, unfound = spans_for_edits(fixed, eds or [])
        calls[-1]["unfound"] = len(unfound)
        llm_pos += [(fixed_off + s, fixed_off + e) for s, e, _, _ in sp]
        gl_all += [(orig_off + s, orig_off + e, r) for s, e, r in gl]
        final = apply_spans(fixed, [(s, e, r) for s, e, r, _ in sp])
        origs.append(orig)
        finals.append(final)
        fixed_off += len(fixed) + 1
        orig_off += len(orig) + 1
    ow, fw = " ".join(origs), " ".join(finals)
    diff = spans_from_diff(ow, fw)
    per_gold, inside_llm, straddle = {}, 0, 0
    for g in w["golds"]:
        off = len(" ".join(cues[w["a"]:g["ca"]])) + (1 if g["ca"] > w["a"] else 0)
        end = off + len(TEXT[g["id"]])
        assert ow[off:end] == TEXT[g["id"]], (w["id"], g["id"])
        per_gold[g["id"]] = [(s - off, e - off, r) for s, e, r in diff if s >= off and e <= end]
        straddle += sum(1 for s, e, _ in diff if s < end and off < e and not (s >= off and e <= end))
        fo, fe = shift(off, gl_all), shift(end, gl_all)
        inside_llm += sum(1 for s, e in llm_pos if s < fe and fo < e)
    gold_words = sum(len(TEXT[g["id"]].split()) for g in w["golds"])
    return {"spans": per_gold, "calls": calls, "llm_edits": len(llm_pos), "llm_inside": inside_llm,
            "words": len(ow.split()), "gold_words": gold_words, "straddle": straddle}


def baseline_E(edir=os.path.join(RES, "E_all_opus")):
    """Arm E (250-word chunks) as a pseudo-run: one window per gold chunk, position = whole.
    edir: the bakeoff's E_all_opus (r1) or this experiment's rerun results/out_250E_r2."""
    rows = {r["id"]: r for r in read_jsonl(os.path.join(RES, "D_inputs_all.jsonl"))}
    per, calls, llm = {}, [], 0
    for cid, orig in TEXT.items():
        res = json.load(open(os.path.join(edir, cid + ".json")))
        eds = parse_edits(str(res.get("result", "")))
        sp, _ = spans_for_edits(rows[cid]["text"], eds or [])
        llm += len(sp)
        per[cid] = spans_from_diff(orig, apply_spans(rows[cid]["text"], [(s, e, r) for s, e, r, _ in sp]))
        u = res.get("usage") or {}
        calls.append({"wall_s": res["wall_s"], "usd": res.get("total_cost_usd", 0), "words": len(orig.split()),
                      "in": sum(u.get(x, 0) for x in ("input_tokens", "cache_creation_input_tokens",
                                                       "cache_read_input_tokens")),
                      "out": u.get("output_tokens", 0), "thinking": (u.get("output_tokens_details") or {}).get("thinking_tokens", 0),
                      "stop": res.get("stop_reason"), "parsed": eds is not None})
    words = sum(len(t.split()) for t in TEXT.values())
    return per, calls, {"llm_per_1k_inside": round(1000 * llm / words, 2)}


def edit_key_set(per):
    return {(cid, s, e, norm(r)) for cid, sp in per.items() for s, e, r in sp if norm(TEXT[cid][s:e]) != norm(r)}


def summarize(per, pos, calls, extra):
    out = {"golds_scored": len(per)}
    rows_by = {}
    for gname, G in GOLDS.items():
        rows = {cid: score_chunk(cid, sp, G) for cid, sp in per.items()}
        rows_by[gname] = rows
        allrows = [r for v in rows.values() for r in v]
        out[gname] = prf(allrows)
        for third in ("first", "middle", "last"):
            rs = [r for cid, v in rows.items() if pos.get(cid) == third for r in v]
            out[gname][f"R_{third}"] = prf(rs)["R"]
            out[gname][f"n_{third}"] = sum(1 for r in rs if r["kind"] in ("TP", "FN"))
    out["calls"] = len(calls)
    out["unparseable"] = sum(not c["parsed"] for c in calls)
    out["not_end_turn"] = sum(c["stop"] != "end_turn" for c in calls)
    for k in ("wall_s", "usd", "in", "out", "thinking", "words"):
        out[f"mean_{k}"] = round(st.mean(c[k] for c in calls), 4)
    out["max_wall_s"] = max(c["wall_s"] for c in calls)
    out.update(extra)
    return out, rows_by


def third_of(p):
    return "first" if p < 1 / 3 else "middle" if p < 2 / 3 else "last"


def boot_delta(rows_a, rows_b, n=2000, seed=1):
    """Paired bootstrap over gold chunks of F1(b) - F1(a). Returns (mean, 2.5%, 97.5%)."""
    ids = sorted(set(rows_a) & set(rows_b))
    rng = random.Random(seed)
    ds = []
    for _ in range(n):
        pick = [rng.choice(ids) for _ in ids]
        fa = prf([r for c in pick for r in rows_a[c]])["F1"]
        fb = prf([r for c in pick for r in rows_b[c]])["F1"]
        ds.append(fb - fa)
    ds.sort()
    return round(st.mean(ds), 3), round(ds[int(0.025 * n)], 3), round(ds[int(0.975 * n)], 3)


def main():
    summary, rows_all, sets = {}, {}, {}
    per, calls, extra = baseline_E()
    s, rows_all["250"] = summarize(per, {}, calls, extra)
    s["size"], s["rep"] = 250, "E"
    summary["250"] = s
    sets["250"] = edit_key_set(per)
    e2 = os.path.join(OUT, "out_250E_r2")
    if len(glob.glob(os.path.join(e2, "*.json"))) == len(TEXT):
        per, calls, extra = baseline_E(e2)
        s, rows_all["250_r2"] = summarize(per, {}, calls, extra)
        s["size"], s["rep"] = 250, "E_r2"
        s["delta_F1_vs_250_adjudicated"] = boot_delta(rows_all["250"]["adjudicated"], rows_all["250_r2"]["adjudicated"])
        summary["250_r2"] = s
        sets["250_r2"] = edit_key_set(per)
    for outdir in sorted(glob.glob(os.path.join(OUT, "out_*_r*"))):
        size, rep = os.path.basename(outdir)[4:].rsplit("_", 1)
        if size == "250E":
            continue
        wins = read_jsonl(os.path.join(OUT, f"windows_{size}.jsonl"))
        per, pos, calls = {}, {}, []
        llm, llm_in, words, gwords, strad, missing = 0, 0, 0, 0, 0, 0
        for w in wins:
            r = run_window(w, outdir)
            if r is None:
                missing += 1
                continue
            per.update(r["spans"])
            for g in w["golds"]:
                pos[g["id"]] = third_of(g["pos"])
            calls += r["calls"]
            llm += r["llm_edits"]
            llm_in += r["llm_inside"]
            words += r["words"]
            gwords += r["gold_words"]
            strad += r["straddle"]
        if missing:
            print(f"{size} {rep}: {missing} windows incomplete, skipped")
            if not per:
                continue
        extra = {"windows": len(wins) - missing, "windows_missing": missing, "straddling_edits": strad,
                 "llm_per_1k_outside": round(1000 * (llm - llm_in) / max(words - gwords, 1), 2),
                 "llm_per_1k_inside": round(1000 * llm_in / max(gwords, 1), 2),
                 "mean_window_words": round(words / max(len(wins) - missing, 1))}
        key = f"{size}_{rep}"
        s, rows_all[key] = summarize(per, pos, calls, extra)
        s["size"], s["rep"] = size, rep
        s["delta_F1_vs_250_adjudicated"] = boot_delta(rows_all["250"]["adjudicated"], rows_all[key]["adjudicated"])
        summary[key] = s
        sets[key] = edit_key_set(per)
    # run-to-run agreement of in-chunk edit sets
    for key in list(summary):
        r1 = "250" if key == "250_r2" else key.replace("_r2", "_r1")
        if key.endswith("_r2") and r1 in sets:
            a, b = sets[r1], sets[key]
            summary[key]["jaccard_vs_r1"] = round(len(a & b) / len(a | b), 3) if a | b else 1.0
    json.dump(summary, open(os.path.join(OUT, "summary.json"), "w"), indent=1)
    json.dump({k: {g: v for g, v in r.items()} for k, r in rows_all.items()},
              open(os.path.join(OUT, "rows.json"), "w"))
    hdr = ("| run | calls | F1 adj | P adj | R adj | F1 Fable | R first/mid/last (adj) | ΔF1 vs 250 [95% CI] | "
           "LLM edits/1k in chunk | outside | s/call | in tok | out tok | $/call | unparsed |")
    lines = [hdr, "|" + "|".join(["---"] * (hdr.count("|") - 1)) + "|"]
    for key, s in summary.items():
        a, f = s["adjudicated"], s["fable"]
        d = s.get("delta_F1_vs_250_adjudicated")
        lines.append(
            f"| {key} | {s['calls']} | {a['F1']} | {a['P']} | {a['R']} | {f['F1']} | "
            f"{a['R_first']}/{a['R_middle']}/{a['R_last']} (n {a['n_first']}/{a['n_middle']}/{a['n_last']}) | "
            f"{'' if d is None else f'{d[0]:+} [{d[1]:+}, {d[2]:+}]'} | {s.get('llm_per_1k_inside', '')} | "
            f"{s.get('llm_per_1k_outside', '')} | {s['mean_wall_s']} | {round(s['mean_in'])} | {round(s['mean_out'])} | "
            f"{s['mean_usd']} | {s['unparseable']} |")
    open(os.path.join(OUT, "table.md"), "w").write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
