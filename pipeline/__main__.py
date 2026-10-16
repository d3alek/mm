"""python -m pipeline {fetch,records,opus-pass,site} [options]"""

import argparse
import json
import os
import time

from . import build_site, fetch, opus_pass, records


def main():
    ap = argparse.ArgumentParser(prog="pipeline")
    ap.add_argument("step", choices=["fetch", "records", "opus-pass", "site"])
    ap.add_argument("--raw", default=os.environ.get("MM_RAW", "data/raw"), help="dir holding MMTranscripts.zip")
    ap.add_argument("--build", default="build")
    ap.add_argument("--dist", default="dist")
    ap.add_argument("--base", default=os.environ.get("SITE_BASE", "/"), help="URL path the site is served under")
    ap.add_argument("--ids", nargs="+", help="opus-pass: only these record ids")
    ap.add_argument("--limit", type=int, help="opus-pass: run at most N pending chunks")
    ap.add_argument("--parallel", type=int, default=6)
    ap.add_argument("--timeout", type=int, default=300, help="opus-pass: seconds per CLI call")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    t0 = time.time()
    if a.step == "fetch":
        fetch.fetch(a.raw)
    elif a.step == "records":
        stats = records.build(os.path.join(a.raw, "MMTranscripts.zip"), a.build, fetch.read_etag(a.raw))
        print(json.dumps(stats, indent=1))
    elif a.step == "opus-pass":
        opus_pass.main(a.build, a.ids, a.limit, a.parallel, a.timeout, a.dry_run)
    else:
        n = build_site.build(a.build, a.dist, a.base, os.environ.get("CF_BEACON_TOKEN", "").strip())
        print(f"site: {n} talk pages")
    print(f"{a.step}: {time.time() - t0:.1f}s")


main()
