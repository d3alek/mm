"""Glossary pass exactly as arm E: glossary_C_opus (byte-identical to pipeline/glossary.json) applied with
common.spans_for_entries on the joined text."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TF = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(TF, "scripts"))
from common import RES, apply_spans, compile_entries, spans_for_entries  # noqa: E402

COMP = compile_entries(json.load(open(os.path.join(RES, "glossary_C_opus.json"))))


def glossary_spans(text):
    return spans_for_entries(text, COMP)


def fixed_text(text):
    return apply_spans(text, glossary_spans(text))
