"""Run claude -p jobs with bounded parallelism. Idempotent: skips jobs whose output exists.
usage: python3 -I run_llm.py jobs.jsonl outdir [parallel=6] [timeout=300]
jobs.jsonl rows: {"id", "model", "system", "prompt", ["effort"]}
Each output: outdir/<id>.json = CLI json result + wall seconds."""
import concurrent.futures as cf
import json
import os
import subprocess
import sys
import time

jobs_path, outdir = sys.argv[1], sys.argv[2]
par = int(sys.argv[3]) if len(sys.argv) > 3 else 6
tmo = int(sys.argv[4]) if len(sys.argv) > 4 else 300
os.makedirs(outdir, exist_ok=True)
CWD = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cli_cwd")
os.makedirs(CWD, exist_ok=True)
jobs = [json.loads(l) for l in open(jobs_path) if l.strip()]


def run(job):
    out = os.path.join(outdir, job["id"] + ".json")
    if os.path.exists(out):
        return job["id"], "cached"
    cmd = ["claude", "-p", "--model", job["model"], "--tools", "", "--strict-mcp-config",
           "--no-session-persistence", "--setting-sources", "", "--output-format", "json",
           "--system-prompt", job["system"]]
    if job.get("effort"):
        cmd += ["--effort", job["effort"]]
    for attempt in range(3):
        t0 = time.time()
        try:
            p = subprocess.run(cmd, input=job["prompt"], capture_output=True, text=True, timeout=tmo, cwd=CWD)
            res = json.loads(p.stdout)
            if res.get("is_error"):
                raise RuntimeError(res.get("result", "error")[:200])
            res["wall_s"] = round(time.time() - t0, 2)
            res["attempt"] = attempt
            json.dump(res, open(out, "w"))
            return job["id"], "ok"
        except Exception as e:  # noqa: BLE001
            err = repr(e)[:200]
            time.sleep(5 * (attempt + 1))
    return job["id"], "FAIL " + err


t0 = time.time()
with cf.ThreadPoolExecutor(par) as ex:
    stat = {}
    for i, (jid, s) in enumerate(ex.map(run, jobs)):
        stat[s.split()[0]] = stat.get(s.split()[0], 0) + 1
        if s.startswith("FAIL"):
            print(jid, s, flush=True)
print(stat, "wall", round(time.time() - t0, 1), "s")
