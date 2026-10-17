"""Corpus-wide precision of a glossary: count every firing, draw a seeded random sample of firings,
write judge jobs (Fable). usage:
  glossary_audit.py prepare GLOSSARY N   -> results/audit_<g>.jsonl + results/jobs_audit_<g>.jsonl
  glossary_audit.py tally GLOSSARY       -> prints verdict counts from results/audit_<g>/"""
import collections
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import CORPUS, DOMAIN_HINT, RES, compile_entries, parse_json_list, parse_srt, read_jsonl, \
    spans_for_entries, srt_files, write_jsonl  # noqa: E402

step, gfile = sys.argv[1], sys.argv[2]
tag = gfile.replace("glossary_", "").replace(".json", "")
SYS = (f"You judge corrections made to speech-to-text (Whisper) transcripts. {DOMAIN_HINT}\n\n"
       "Each item shows a transcript snippet with the original words in [[double brackets]] and the "
       "replacement the script made. Judge: \"right\" if the original was a mis-transcription and the "
       "replacement is what the speaker said; \"wrong\" if the original was correct or the replacement is "
       "not what was said; \"unsure\" if you cannot tell. Capitalisation or hyphenation alone does not matter.\n"
       "Output ONLY a JSON array: [{\"n\": <item number>, \"verdict\": \"right\"|\"wrong\"|\"unsure\"}]")
if step == "prepare":
    n_draw = int(sys.argv[3])
    comp = compile_entries(json.load(open(os.path.join(RES, gfile))))
    fires, per_entry = [], collections.Counter()
    for p in srt_files():
        t = " ".join(parse_srt(os.path.join(CORPUS, p)))
        log = []
        for s, e, r in spans_for_entries(t, comp, log):
            fires.append((p, s, e, r, t[max(0, s - 80):s], t[s:e], t[e:e + 80]))
        for _s, _e, f, _c in log:
            per_entry[f] += 1
    rng = random.Random(11)
    draw = rng.sample(fires, min(n_draw, len(fires)))
    rows = [{"n": i, "file": d[0], "orig": d[5], "repl": d[3],
             "snippet": " ".join(d[4].split()[-10:]) + " [[" + d[5] + "]] " + " ".join(d[6].split()[:10])}
            for i, d in enumerate(draw)]
    write_jsonl(os.path.join(RES, f"audit_{tag}.jsonl"), rows)
    jobs = []
    for i in range(0, len(rows), 50):
        body = "\n".join(f"{r['n']}. {r['snippet']}  =>  replaced with: {r['repl']}" for r in rows[i:i + 50])
        jobs.append({"id": f"a{i // 50:02d}", "model": "claude-fable-5-1", "system": SYS,
                     "prompt": "<items>\n" + body + "\n</items>"})
    write_jsonl(os.path.join(RES, f"jobs_audit_{tag}.jsonl"), jobs)
    json.dump({"total_firings": len(fires), "entries_firing": len(per_entry),
               "top": per_entry.most_common(30)}, open(os.path.join(RES, f"audit_{tag}_firings.json"), "w"), indent=1)
    print(tag, "firings", len(fires), "entries firing", len(per_entry), "drawn", len(rows))
else:
    rows = {r["n"]: r for r in read_jsonl(os.path.join(RES, f"audit_{tag}.jsonl"))}
    v = {}
    d = os.path.join(RES, f"audit_{tag}")
    for f in sorted(os.listdir(d)):
        for x in parse_json_list(json.load(open(os.path.join(d, f)))["result"]) or []:
            if isinstance(x, dict) and x.get("n") in rows:
                v[x["n"]] = x.get("verdict")
    c = collections.Counter(v.values())
    fir = json.load(open(os.path.join(RES, f"audit_{tag}_firings.json")))
    judged = c["right"] + c["wrong"]
    res = {"glossary": gfile, "total_firings": fir["total_firings"], "judged": dict(c),
           "precision": round(c["right"] / judged, 3) if judged else None,
           "wrong_examples": [rows[n]["snippet"] + " => " + rows[n]["repl"] for n, x in v.items() if x == "wrong"]}
    json.dump(res, open(os.path.join(RES, f"audit_{tag}_result.json"), "w"), indent=1)
    print(json.dumps(res, indent=1)[:3000])
