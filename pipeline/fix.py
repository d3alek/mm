"""Text-fix stage. Pass-through unless `pipeline/glossary.tsv` exists.

Glossary format: one `wrong<TAB>right` pair per line, `#` starts a comment.
Matching is case-insensitive on whole words/phrases; the replacement is used
verbatim. A changed cue keeps its original text in `text_raw`.
"""

import os
import re

DEFAULT = os.path.join(os.path.dirname(__file__), "glossary.tsv")


def load(path: str = DEFAULT) -> list[tuple[re.Pattern, str]]:
    if not os.path.exists(path):
        return []
    rules = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            wrong, right = line.split("\t")[:2]
            rules.append((re.compile(rf"(?i)(?<!\w){re.escape(wrong.strip())}(?!\w)"), right.strip()))
    return rules


def apply(rec: dict, rules) -> dict:
    if not rules:
        return rec
    changed = 0
    for cue in rec["cues"]:
        text = cue["text"]
        for pattern, right in rules:
            text = pattern.sub(right, text)
        if text != cue["text"]:
            cue["text_raw"] = cue["text"]
            cue["text"] = text
            changed += 1
    if changed:
        rec["flags"] = sorted(set(rec["flags"]) | {"glossary_fixed"})
    return rec
