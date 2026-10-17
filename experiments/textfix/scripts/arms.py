"""Load each arm's edits as spans on the sample chunks.
Every arm returns {chunk_id: [(start, end, replacement), ...]} on the original chunk text."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (RES, apply_spans, compile_entries, parse_json_list, read_jsonl, spans_for_edits,  # noqa: E402
                    spans_for_entries, spans_from_diff)

sample = read_jsonl(os.path.join(RES, "sample.jsonl"))
TEXT = {s["id"]: s["text"] for s in sample}


def glossary_arm(path, cond_log=None):
    """cond_log: optional dict chunk -> list of (s, e) spans fired by context-conditioned entries."""
    g = json.load(open(os.path.join(RES, path)))
    comp = compile_entries(g)
    out = {}
    for cid, t in TEXT.items():
        log = []
        out[cid] = spans_for_entries(t, comp, log)
        if cond_log is not None:
            cond_log[cid] = [(s, e) for s, e, _f, c in log if c]
    return out


def llm_raw(run_dir):
    """chunk_id -> parsed edit list (None if unparseable or missing)."""
    out = {}
    for cid in TEXT:
        p = os.path.join(RES, run_dir, cid + ".json")
        out[cid] = parse_json_list(json.load(open(p))["result"]) if os.path.exists(p) else None
    return out


def llm_arm(run_dir):
    raw = llm_raw(run_dir)
    spans, unfound, bad = {}, 0, 0
    for cid, eds in raw.items():
        if eds is None:
            bad += 1
            spans[cid] = []
            continue
        sp, uf = spans_for_edits(TEXT[cid], [e for e in eds if isinstance(e, dict)])
        unfound += len(uf)
        spans[cid] = [(s, e, r) for s, e, r, _ in sp]
    return spans, {"unparseable_or_missing": bad, "edits_not_found_in_text": unfound}


def cascade_arm(resid_dir, inputs="D_inputs.jsonl"):
    """D: glossary-fixed text from D_inputs.jsonl + residual LLM edits on triggered chunks.
    Returns spans on the original text (word diff), plus the raw residual edits."""
    rows = {r["id"]: r for r in read_jsonl(os.path.join(RES, inputs))}
    spans, resid = {}, {}
    for cid, orig in TEXT.items():
        fixed = rows[cid]["text"]
        if rows[cid]["triggered"]:
            p = os.path.join(RES, resid_dir, cid + ".json")
            eds = parse_json_list(json.load(open(p))["result"]) if os.path.exists(p) else None
            eds = [e for e in (eds or []) if isinstance(e, dict)]
            sp, _ = spans_for_edits(fixed, eds)
            resid[cid] = [{"from": fixed[s:e], "to": r} for s, e, r, _ in sp]
            fixed = apply_spans(fixed, sp)
        spans[cid] = spans_from_diff(orig, fixed)
    return spans, resid


def gold(path):
    """chunk_id -> list of (s, e, repl, confidence, from)."""
    g = json.load(open(os.path.join(RES, path)))
    out = {}
    for cid, eds in g.items():
        sp, _ = spans_for_edits(TEXT[cid], eds)
        out[cid] = [(s, e, r, ed.get("confidence", "sure"), ed["from"]) for s, e, r, ed in sp]
    return out
