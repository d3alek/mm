"""run_llm.py with a memory gate. Same CLI flags as run_llm.py / pipeline/opus_pass.py (tools disabled,
no effort flag). Idempotent: skips jobs whose output exists. Never more than 3 concurrent calls, and a call
starts only while MemAvailable >= 3 GB (this laptop has 14 GB; a run was killed for low memory).
An unparseable answer is stored as-is (no retry), so truncation shows up in the score.

usage: python3 -I run.py jobs.jsonl outdir [parallel<=3] [timeout=900]"""
import concurrent.futures as cf
import json
import os
import subprocess
import sys
import threading
import time

jobs_path, outdir = sys.argv[1], sys.argv[2]
par = min(int(sys.argv[3]) if len(sys.argv) > 3 else 3, 3)
tmo = int(sys.argv[4]) if len(sys.argv) > 4 else 900
MIN_AVAIL_KB = 3 * 1024 * 1024
os.makedirs(outdir, exist_ok=True)
CWD = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cli_cwd")
os.makedirs(CWD, exist_ok=True)
jobs = [json.loads(l) for l in open(jobs_path) if l.strip()]
gate = threading.Lock()


def mem_available_kb():
    for line in open("/proc/meminfo"):
        if line.startswith("MemAvailable:"):
            return int(line.split()[1])
    return 0


def wait_for_memory():
    with gate:  # one waiter at a time, and a short gap so the last started call shows up in meminfo
        while mem_available_kb() < MIN_AVAIL_KB:
            print(f"low memory ({mem_available_kb() // 1024} MB available), waiting", flush=True)
            time.sleep(30)
        time.sleep(2)


def run(job):
    out = os.path.join(outdir, job["id"] + ".json")
    if os.path.exists(out):
        return job["id"], "cached"
    cmd = ["claude", "-p", "--model", job["model"], "--tools", "", "--strict-mcp-config",
           "--no-session-persistence", "--setting-sources", "", "--output-format", "json",
           "--system-prompt", job["system"]]
    err = ""
    for attempt in range(4):
        wait_for_memory()
        t0 = time.time()
        try:
            p = subprocess.run(cmd, input=job["prompt"], capture_output=True, text=True, timeout=tmo, cwd=CWD)
            res = json.loads(p.stdout)
            if res.get("is_error"):
                raise RuntimeError(str(res.get("result", "error"))[:200])
            res["wall_s"] = round(time.time() - t0, 2)
            res["attempt"] = attempt
            json.dump(res, open(out + ".tmp", "w"))
            os.replace(out + ".tmp", out)
            return job["id"], "ok"
        except Exception as e:  # noqa: BLE001
            err = repr(e)[:200]
            print(job["id"], "attempt", attempt, err, flush=True)
            if "limit" in err.lower() and "rate" not in err.lower():
                time.sleep(900)
            else:
                time.sleep(15 * (attempt + 1))
    return job["id"], "FAIL " + err


t0 = time.time()
stat = {}
with cf.ThreadPoolExecutor(par) as ex:
    for n, (jid, s) in enumerate(ex.map(run, jobs), 1):
        stat[s.split()[0]] = stat.get(s.split()[0], 0) + 1
        if s.startswith("FAIL"):
            print(jid, s, flush=True)
        if n % 10 == 0:
            print(f"{n}/{len(jobs)} {stat} {round(time.time() - t0)}s", flush=True)
print(stat, "wall", round(time.time() - t0, 1), "s", flush=True)
