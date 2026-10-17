"""Download the transcripts zip with a conditional GET on its ETag."""

import os
import urllib.error
import urllib.request

URL = "https://downloads.miguelhx.com/MMTranscripts.zip"


def fetch(raw_dir: str, url: str = URL) -> bool:
    """Return True when a new zip was downloaded, False when unchanged."""
    os.makedirs(raw_dir, exist_ok=True)
    zip_path = os.path.join(raw_dir, "MMTranscripts.zip")
    etag_path = os.path.join(raw_dir, "etag.txt")
    req = urllib.request.Request(url, headers={"User-Agent": "mm-pipeline"})
    if os.path.exists(zip_path) and os.path.exists(etag_path):
        with open(etag_path) as f:
            req.add_header("If-None-Match", f.read().strip())
    try:
        resp = urllib.request.urlopen(req, timeout=120)
    except urllib.error.HTTPError as e:
        if e.code == 304:
            print("fetch: unchanged (304)")
            return False
        raise
    tmp = zip_path + ".part"
    with resp, open(tmp, "wb") as out:
        while chunk := resp.read(1 << 20):
            out.write(chunk)
    os.replace(tmp, zip_path)
    with open(etag_path, "w") as f:
        f.write((resp.headers.get("ETag") or "") + "\n")
    print(f"fetch: downloaded {os.path.getsize(zip_path)} bytes")
    return True


def read_etag(raw_dir: str) -> str | None:
    try:
        with open(os.path.join(raw_dir, "etag.txt")) as f:
            return f.read().strip() or None
    except FileNotFoundError:
        return None
