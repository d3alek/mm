"""Read SRT files straight out of the zip (never extracted) and parse cues."""

import re
import zipfile

PREFIX = "TranscriptsSrt/"
TIME = re.compile(
    r"(\d+):(\d\d):(\d\d)[,.](\d{1,3})\s*-->\s*(\d+):(\d\d):(\d\d)[,.](\d{1,3})"
)


def _name(info: zipfile.ZipInfo) -> str:
    # The zip omits the UTF-8 flag, so Python decodes names as cp437.
    if info.flag_bits & 0x800:
        return info.filename
    try:
        return info.filename.encode("cp437").decode("utf-8")
    except UnicodeError:
        return info.filename


def iter_srt(zip_path: str):
    """Yield (path relative to TranscriptsSrt without .srt, text), sorted by path."""
    with zipfile.ZipFile(zip_path) as z:
        entries = sorted(
            (_name(i), i)
            for i in z.infolist()
            if not i.is_dir() and _name(i).startswith(PREFIX) and _name(i).endswith(".srt")
        )
        for name, info in entries:
            text = z.read(info).decode("utf-8", errors="replace")
            yield name[len(PREFIX) : -len(".srt")], text


def _secs(h, m, s, ms):
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000


def parse_srt(text: str) -> list[dict]:
    cues = []
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = block.split("\n")
        for i, line in enumerate(lines[:2]):
            m = TIME.search(line)
            if m:
                body = " ".join(l.strip() for l in lines[i + 1 :]).strip()
                cues.append(
                    {"t": round(_secs(*m.groups()[:4]), 2), "e": round(_secs(*m.groups()[4:]), 2), "text": body}
                )
                break
    return cues
