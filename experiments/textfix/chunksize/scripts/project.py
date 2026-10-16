"""Full-corpus projection per chunk size. Reads build/records and pipeline/fixes READ-ONLY (never calls the
Runner, whose plan() rewrites fix files).

Remaining work = the 250-word production chunks with no line in pipeline/fixes, merged into runs of
consecutive undone chunks per record; each run is re-cut with the production rule (cues until >= N words).
Seconds and dollars per call come from a least-squares line (a + b*words) fitted on this experiment's calls
(per-run means weighted by call count: r1 of every size plus arm E; r2 reruns hit the prompt cache), so short tail chunks are
priced as what they are.
usage: python3 -I project.py -> results/projection.json"""
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
sys.path.insert(0, REPO)
from pipeline import fix, opus_pass  # noqa: E402

OUT = os.path.join(os.path.dirname(HERE), "results")
TF = os.path.dirname(os.path.dirname(HERE))
SIZES = [250, 1000, 2000, 4000, 8000, 16000]


def recut(raw, n):
    out, i = [], 0
    while i < len(raw):
        j, k = i, 0
        while j < len(raw) and k < n:
            k += len(raw[j].split())
            j += 1
        out.append(k)
        i = j
    return out


def fit(xs, ys):
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
    return my - b * mx, b


def main():
    rules = fix.load()
    remaining_runs, done_chunks, total_chunks, rem_words, all_words = [], 0, 0, 0, 0
    for p in sorted(glob.glob(os.path.join(REPO, "build", "records", "*.json"))):
        rec = json.load(open(p, encoding="utf-8"))
        raw = [c.get("text_raw", c["text"]) for c in rec["cues"]]
        fixed = fix.glossary_texts(rec["cues"], rules)
        chs = opus_pass.chunks(raw, fixed)
        fp = os.path.join(fix.FIXES, rec["id"] + ".jsonl")
        done = set()
        if os.path.exists(fp):
            done = {json.loads(l).get("sha") for l in open(fp, encoding="utf-8") if l.strip()}
        run = []
        for i, j, _, sha in chs:
            total_chunks += 1
            w = sum(len(t.split()) for t in raw[i:j])
            all_words += w
            if sha in done:
                done_chunks += 1
                if run:
                    remaining_runs.append(run)
                    run = []
            else:
                rem_words += w
                run += raw[i:j]
        if run:
            remaining_runs.append(run)
    # cost / time model from measured calls
    out = {"chunks_total": total_chunks, "chunks_done": done_chunks, "words_total": all_words,
           "words_remaining": rem_words, "sizes": {}}
    summ = json.load(open(os.path.join(OUT, "summary.json")))
    pts = []  # (mean words, mean wall_s, mean usd, calls) per run
    for key, s in summ.items():
        if key != "250" and not key.endswith("_r1"):
            continue  # r2 repeats r1's exact prompts within the cache window: cache-read prices, not list
        pts.append((s["mean_words"], s["mean_wall_s"], s["mean_usd"], s["calls"]))
    xs = [p[0] for p in pts for _ in range(p[3])]
    wa, wb = fit(xs, [p[1] for p in pts for _ in range(p[3])])
    ua, ub = fit(xs, [p[2] for p in pts for _ in range(p[3])])
    out["model"] = {"wall_s": [round(wa, 3), round(wb * 1000, 3)], "usd": [round(ua, 5), round(ub * 1000, 5)],
                    "note": "a + b per 1k words, fitted on per-run means weighted by calls"}
    for n in SIZES:
        sizes = [k for r in remaining_runs for k in recut(r, n)]
        wall = sum(wa + wb * k for k in sizes)
        usd = sum(ua + ub * k for k in sizes)
        out["sizes"][n] = {"calls": len(sizes), "wall_h_3way": round(wall / 3 / 3600, 1),
                           "wall_h_6way": round(wall / 6 / 3600, 1), "list_usd": round(usd)}
    json.dump(out, open(os.path.join(OUT, "projection.json"), "w"), indent=1)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
