"""Arm E = glossary (C_opus) pass, then the LLM on EVERY chunk (cascade with trigger = all).
Writes results/D_inputs_all.jsonl (same fixed text as D, all chunks marked triggered)."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import RES, read_jsonl, write_jsonl  # noqa: E402

rows = read_jsonl(os.path.join(RES, "D_inputs.jsonl"))
for r in rows:
    r["triggered"] = True
write_jsonl(os.path.join(RES, "D_inputs_all.jsonl"), rows)
print(len(rows))
