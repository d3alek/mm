#!/usr/bin/env python3
"""Sample N `high` rows from links.csv (seeded) and print transcript vs listing facts.

YouTube flat listings carry no dates, so the sample's upload dates are fetched one video at a
time (cached in cache/spot_<id>.json). Usage: python3 -I spotcheck.py [N] [seed]
"""
import csv, json, os, random, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
n = int(sys.argv[1]) if len(sys.argv) > 1 else 15
seed = int(sys.argv[2]) if len(sys.argv) > 2 else 1
rows = [r for r in csv.DictReader(open(os.path.join(HERE, 'links.csv'))) if r['match'] == 'high']
for r in random.Random(seed).sample(rows, n):
    date = r['listing_date']
    if r['provider'] == 'youtube':
        path = os.path.join(HERE, 'cache', f'spot_{r["video_id"]}.json')
        if not os.path.exists(path):
            out = subprocess.run(['yt-dlp', '--no-update', '--skip-download', '-j', r['url']],
                                 capture_output=True, timeout=300).stdout
            with open(path, 'wb') as f:
                f.write(out)
        try:
            meta = json.load(open(path))
            d = meta.get('release_date') or meta.get('upload_date') or ''
            date = f'{d[:4]}-{d[4:6]}-{d[6:]} (dur {meta.get("duration")})'
        except ValueError:
            date = 'unavailable'
    print(f'{r["path"]}\n    -> [{r["provider"]}] {r["listing_title"]} | {date} | dDur={r["duration_delta_s"]} | {r["url"]}')
