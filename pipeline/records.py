"""zip → normalized, fixed, deduped records in build/records + build/manifest.json."""

import collections
import csv
import hashlib
import json
import os

from . import fix, normalize, parse

LINKS_CSV = "links/links.csv"


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"


def _write_if_changed(path: str, data: str):
    try:
        with open(path, encoding="utf-8") as f:
            if f.read() == data:
                return
    except FileNotFoundError:
        pass
    with open(path, "w", encoding="utf-8") as f:
        f.write(data)


def dedupe(recs: list[dict]) -> list[dict]:
    """Merge exact text duplicates; the dated, shortest path wins, the rest become aliases."""
    groups = collections.defaultdict(list)
    out = []
    for r in recs:
        if "empty" in r["flags"]:
            out.append(r)
        else:
            groups[r["content_sha"]].append(r)
    for group in groups.values():
        group.sort(key=lambda r: (r["date_precision"] != "day", len(r["path"]), r["path"]))
        keep = group[0]
        keep["aliases"] = sorted(r["path"] for r in group[1:])
        out.append(keep)
    for r in out:
        r["id"] = hashlib.sha1(r["path"].encode()).hexdigest()[:12]
    return sorted(out, key=lambda r: r["id"])


def load_links(path: str = LINKS_CSV) -> dict[str, dict]:
    """links.csv columns: id or path, provider, video_id, url, match, duration_delta_s."""
    if not os.path.exists(path):
        return {}
    links = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            key = (row.get("id") or row.get("path") or "").strip().removesuffix(".srt")
            if key and row.get("video_id", "").strip():
                links[key] = {
                    "provider": row.get("provider", "youtube").strip() or "youtube",
                    "id": row["video_id"].strip(),
                    "url": (row.get("url") or "").strip() or None,
                    "match": (row.get("match") or "manual").strip(),
                    # A recording much shorter or longer than the transcript is a different cut, so offsets drift.
                    "timestamps": abs(float(row.get("duration_delta_s") or 0)) <= 60,
                }
    return links


def build(zip_path: str, out_dir: str, etag: str | None, links_path: str = LINKS_CSV) -> dict:
    rules = fix.load()
    recs = [fix.apply(normalize.record(p, parse.parse_srt(t)), rules) for p, t in parse.iter_srt(zip_path)]
    n_files = len(recs)
    recs = dedupe(recs)
    links = load_links(links_path)
    for r in recs:
        r["video"] = next((links[k] for k in (r["id"], r["path"], *r["aliases"]) if k in links), None)

    rec_dir = os.path.join(out_dir, "records")
    os.makedirs(rec_dir, exist_ok=True)
    wanted = set()
    for r in recs:
        name = r["id"] + ".json"
        wanted.add(name)
        _write_if_changed(os.path.join(rec_dir, name), _dump(r))
    for name in os.listdir(rec_dir):
        if name not in wanted:
            os.remove(os.path.join(rec_dir, name))

    dated = sum(r["date_precision"] == "day" for r in recs)
    stats = {
        "srt_files": n_files,
        "records": len(recs),
        "merged_duplicates": n_files - len(recs),
        "duplicate_groups": sum(bool(r["aliases"]) for r in recs),
        "date_day": dated,
        "date_year_only": sum(r["date_precision"] == "year" for r in recs),
        "episode": sum(r["episode"] is not None for r in recs),
        "repeat_collapsed": sum("repeat_collapsed" in r["flags"] for r in recs),
        "empty": sum("empty" in r["flags"] for r in recs),
        "glossary_rules": len(rules),
        "video_links": sum(r["video"] is not None for r in recs),
    }
    manifest = {
        "source": {"url": "https://downloads.miguelhx.com/MMTranscripts.zip", "etag": etag},
        "stats": stats,
        "talks": [{k: v for k, v in r.items() if k != "cues"} for r in recs],
    }
    _write_if_changed(os.path.join(out_dir, "manifest.json"), _dump(manifest))
    return stats
