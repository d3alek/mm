"""Turn a source path + parsed cues into a normalized talk record."""

import datetime
import hashlib
import re

REPEAT_RUN = 4  # Whisper hallucination: the same cue 4+ times in a row
SHORT_WORDS = 30
MAX_YEAR = 2030


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def _date(y, m, d):
    try:
        return datetime.date(y, m, d)
    except ValueError:
        return None


def _leading_date(stem: str, folder_year: int | None):
    """`yy-m-d` (most) or `m-d-yy` (2026 and a few late 2025). Returns (date, source, rest)."""
    m = re.match(r"^(\d{1,2})-(\d{1,2})-(\d{1,2})(?!\d)\s*(?:[-–]\s+)?", stem)
    if not m:
        return None, None, stem
    a, b, c = map(int, m.groups())
    ymd = _date(2000 + a, b, c) if 14 <= a <= MAX_YEAR - 2000 else None
    mdy = _date(2000 + c, a, b) if 14 <= c <= MAX_YEAR - 2000 else None
    if ymd and mdy and folder_year:
        ymd, mdy = (ymd, None) if ymd.year == folder_year else (None, mdy)
    if ymd and not mdy:
        return ymd, "ymd", stem[m.end() :]
    if mdy and not ymd:
        return mdy, "mdy", stem[m.end() :]
    return None, None, stem


def clean_title(t: str) -> str:
    t = t.replace("⧸", "/").replace("⎜", "|")
    # Filenames replace ?:/ with `_`; guess which one from position.
    if " " not in t.strip():
        t = t.replace("_", " ")
    t = re.sub(r"_ ", ": ", t)
    t = re.sub(r"_$", "?", t.strip())
    t = t.replace("_", " ")
    t = re.sub(r"\s+", " ", t).strip(" -–")
    return t


def describe(path: str) -> dict:
    """Metadata from the path `Top/Sub/.../transcripts/Name`."""
    parts = path.split("/")
    top, dirs, stem = parts[0], parts[1:-1], parts[-1]
    folder_year = next((int(d) for d in dirs if re.fullmatch(r"20\d\d", d)), None)
    variant = None
    series_dirs = []
    for d in dirs:
        if d == "transcripts" or re.fullmatch(r"20\d\d", d):
            continue
        m = re.fullmatch(r"(Youtube|Instagram) Version", d)
        if m:
            variant = m[1].lower()
            continue
        series_dirs.append(d)

    flags = []
    t = stem
    m = re.search(r"\s*\[(audio|video)\]", t)
    if m:
        variant = m[1]
        t = t[: m.start()] + t[m.end() :]
    t = re.sub(r"\s*\(\d\)$", "", t)
    m = re.search(r"\s+(?:-\s+)?(YT|IG)$", t)
    if m:
        variant = variant or {"YT": "youtube", "IG": "instagram"}[m[1]]
        t = t[: m.start()]

    date, source, t = _leading_date(t, folder_year)
    precision = "day" if date else None
    date_s = date.isoformat() if date else None
    episode = None
    if not date:
        m = re.search(r"(?<!\d)(\d{1,2})-(\d{1,2})-(20\d\d)(?!\d)", t)
        if m and (d := _date(int(m[3]), int(m[1]), int(m[2]))):
            date_s, precision, source = d.isoformat(), "day", "mdy"
    if not date_s:
        m = re.match(r"^(\d{3}) (?=\S)", t)
        if m:
            episode = int(m[1])
            source = "episode"
            t = t[m.end() :]
            if len(stem) == 32:
                flags.append("title_truncated")
    if not date_s:
        m = re.match(r"^(\d{2}) - ", t)
        if m and 14 <= int(m[1]) <= MAX_YEAR - 2000:
            date_s, precision, source = str(2000 + int(m[1])), "year", "yy"
            t = t[m.end() :]
        elif folder_year:
            date_s, precision, source = str(folder_year), "year", "folder"

    title = clean_title(t) or clean_title(stem)
    m = re.search(r"(?i)\b(?:part|pt\.?)\s*(\d+)\b", title)
    return {
        "platform": slug(top),
        "series": " / ".join(series_dirs) or top,
        "variant": variant,
        "title": title,
        "title_raw": stem,
        "date": date_s,
        "date_precision": precision,
        "date_source": source,
        "episode": episode,
        "part": int(m[1]) if m else None,
        "flags": flags,
    }


def clean_cues(cues: list[dict]) -> tuple[list[dict], bool]:
    """Drop empty cues and collapse runs of REPEAT_RUN+ identical cues into one."""
    cues = [dict(c, text=re.sub(r"\s+", " ", c["text"]).strip()) for c in cues]
    cues = [c for c in cues if c["text"]]
    out, collapsed, i = [], False, 0
    while i < len(cues):
        j = i
        key = cues[i]["text"].lower()
        while j + 1 < len(cues) and cues[j + 1]["text"].lower() == key:
            j += 1
        if j - i + 1 >= REPEAT_RUN:
            out.append(dict(cues[i], e=cues[j]["e"]))
            collapsed = True
        else:
            out.extend(cues[i : j + 1])
        i = j + 1
    return out, collapsed


def content_sha(cues: list[dict]) -> str:
    norm = " ".join(" ".join(c["text"] for c in cues).lower().split())
    return hashlib.sha1(norm.encode()).hexdigest()


def record(path: str, cues: list[dict]) -> dict:
    meta = describe(path)
    cues, collapsed = clean_cues(cues)
    flags = meta.pop("flags")
    if collapsed:
        flags.append("repeat_collapsed")
    words = sum(len(c["text"].split()) for c in cues)
    if not cues:
        flags.append("empty")
    elif words < SHORT_WORDS:
        flags.append("short")
    return {
        "id": None,
        "content_sha": content_sha(cues),
        **meta,
        "path": path,
        "aliases": [],
        "video": None,
        "duration_s": int(round(cues[-1]["e"])) if cues else 0,
        "words": words,
        "flags": sorted(flags),
        "cues": cues,
    }
