"""build/records → dist/: talk pages, browse page, search page (Pagefind runs after)."""

import json
import os
import shutil
from html import escape

SECTION_S = 30
SITE_NAME = "MM Transcripts"
STATIC = os.path.join(os.path.dirname(__file__), "..", "site")


def clock(sec: float) -> str:
    sec = int(sec)
    h, m, s = sec // 3600, sec // 60 % 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def platform_name(r: dict) -> str:
    return r["path"].split("/", 1)[0]


def year(r: dict) -> str:
    return r["date"][:4] if r["date"] else "Undated"


def video_url(video: dict | None, sec: int | None = None) -> str | None:
    if not video or video.get("provider") != "youtube" or not video.get("id"):
        return None
    return f"https://www.youtube.com/watch?v={video['id']}" + (f"&t={sec}s" if sec else "")


def page(base: str, title: str, body: str, beacon: str, head: str = "") -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>{escape(title)}</title>
<link rel="stylesheet" href="{base}assets/style.css">
{head}</head>
<body>
<header class="top" data-pagefind-ignore="all"><a class="brand" href="{base}">{SITE_NAME}</a><nav><a href="{base}">Search</a><a href="{base}browse/">Browse</a></nav></header>
{body}
{beacon}</body>
</html>
"""


def talk_page(r: dict, base: str, beacon: str) -> str:
    sections: dict[int, list[str]] = {}
    for c in r["cues"]:
        sections.setdefault(int(c["t"]) // SECTION_S * SECTION_S, []).append(c["text"])
    parts = []
    for sec, texts in sections.items():
        href = video_url(r["video"], sec)
        link = (
            f'<a class="ts" href="{escape(href)}" target="_blank" rel="noopener">{clock(sec)} ▶</a>'
            if href
            else f'<a class="ts" href="#t{sec}">{clock(sec)}</a>'
        )
        parts.append(f'<h3 id="t{sec}">{link}</h3>\n<p>{escape(" ".join(texts))}</p>')

    meta = [r["date"] or "Undated", f"{platform_name(r)} · {r['series']}" if r["series"] != platform_name(r) else platform_name(r)]
    if r["episode"] is not None:
        meta.append(f"Episode {r['episode']}")
    if r["duration_s"]:
        meta.append(clock(r["duration_s"]))
    watch = video_url(r["video"])
    filters = [("platform", platform_name(r)), ("series", r["series"]), ("year", year(r))]
    aliases = "".join(f"<li>{escape(a)}</li>" for a in r["aliases"])
    body = f"""<main class="talk" data-pagefind-body>
<h1>{escape(r["title"])}</h1>
<div hidden data-pagefind-ignore>{"".join(f'<span data-pagefind-filter="{k}">{escape(v)}</span>' for k, v in filters)}<span data-pagefind-meta="date">{escape(r["date"] or "")}</span><span data-pagefind-sort="date">{escape(r["date"] or "0000")}</span></div>
<p class="meta" data-pagefind-ignore>{" · ".join(escape(m) for m in meta)}{f' · <a href="{escape(watch)}" target="_blank" rel="noopener">Watch</a>' if watch else ""}</p>
{f'<details class="aliases" data-pagefind-ignore><summary>Also published as</summary><ul>{aliases}</ul></details>' if aliases else ""}
<p class="source" data-pagefind-ignore>Source file: {escape(r["path"])}</p>
{"".join(parts) or "<p><em>Empty transcript.</em></p>"}
</main>"""
    return page(base, f"{r['title']} · {SITE_NAME}", body, beacon)


def browse_page(talks: list[dict], base: str, beacon: str) -> str:
    talks = sorted(talks, key=lambda r: r["title"])
    talks.sort(key=lambda r: r["date"] or "", reverse=True)  # newest first, undated last

    def options(values):
        return "".join(f'<option value="{escape(v)}">{escape(v)}</option>' for v in values)

    platforms = sorted({platform_name(r) for r in talks})
    series = sorted({(platform_name(r), r["series"]) for r in talks})
    years = sorted({year(r) for r in talks}, reverse=True)
    rows = []
    for r in talks:
        bits = [r["date"] or "Undated", r["series"]]
        if r["duration_s"]:
            bits.append(clock(r["duration_s"]))
        rows.append(
            f'<li data-p="{escape(platform_name(r))}" data-s="{escape(r["series"])}" data-y="{year(r)}">'
            f'<a href="{base}t/{r["id"]}/">{escape(r["title"])}</a><small>{escape(" · ".join(bits))}</small></li>'
        )
    series_opts = "".join(
        f'<option value="{escape(s)}" data-p="{escape(p)}">{escape(s if s == p else f"{p} · {s}")}</option>' for p, s in series
    )
    body = f"""<main class="browse">
<h1>Browse {len(talks)} talks</h1>
<form class="filters" onsubmit="return false">
<input id="q" type="search" placeholder="Filter titles" aria-label="Filter titles">
<select id="fp" aria-label="Platform"><option value="">All platforms</option>{options(platforms)}</select>
<select id="fs" aria-label="Series"><option value="">All series</option>{series_opts}</select>
<select id="fy" aria-label="Year"><option value="">All years</option>{options(years)}</select>
</form>
<p id="count" class="count"></p>
<ul id="list" class="list">
{chr(10).join(rows)}
</ul>
</main>
<script src="{base}assets/browse.js" defer></script>"""
    return page(base, f"Browse · {SITE_NAME}", body, beacon)


def search_page(stats: dict, base: str, beacon: str) -> str:
    body = f"""<main class="search">
<h1>Search {stats["records"]} talks</h1>
<p class="hint">Phrases in quotes match exactly. Results link to the 30-second section where the words occur.</p>
<div id="search" data-base="{base}"></div>
</main>
<script src="{base}pagefind/pagefind-ui.js"></script>
<script src="{base}assets/search.js"></script>"""
    head = f'<link rel="stylesheet" href="{base}pagefind/pagefind-ui.css">\n'
    return page(base, SITE_NAME, body, beacon, head)


def beacon_tag(token: str) -> str:
    if not token:
        return ""
    cfg = escape(json.dumps({"token": token}), quote=True)
    return f'<script defer src="https://static.cloudflareinsights.com/beacon.min.js" data-cf-beacon="{cfg}"></script>\n'


def write(path: str, text: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def build(build_dir: str, dist: str, base: str, cf_token: str) -> int:
    base = "/" + base.strip("/") + "/" if base.strip("/") else "/"
    beacon = beacon_tag(cf_token)
    with open(os.path.join(build_dir, "manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    shutil.rmtree(dist, ignore_errors=True)
    shutil.copytree(STATIC, os.path.join(dist, "assets"))
    talks = [t for t in manifest["talks"] if "empty" not in t["flags"]]
    for t in talks:
        with open(os.path.join(build_dir, "records", t["id"] + ".json"), encoding="utf-8") as f:
            write(os.path.join(dist, "t", t["id"], "index.html"), talk_page(json.load(f), base, beacon))
    write(os.path.join(dist, "browse", "index.html"), browse_page(talks, base, beacon))
    write(os.path.join(dist, "index.html"), search_page(manifest["stats"], base, beacon))
    write(os.path.join(dist, "robots.txt"), "User-agent: *\nDisallow: /\n")
    write(os.path.join(dist, "source.json"), json.dumps(manifest["source"], sort_keys=True) + "\n")
    return len(talks)
