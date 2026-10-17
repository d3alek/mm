# MM_RAW: dir holding MMTranscripts.zip (+ etag.txt). SITE_BASE: URL path prefix.
MM_RAW ?= data/raw
SITE_BASE ?= /
PAGEFIND ?= npx -y pagefind@1.5.2
PY ?= python3
PORT ?= 8000
PARALLEL ?= 3
export MM_RAW SITE_BASE CF_BEACON_TOKEN

.PHONY: fetch records opus-pass data site embed serve clean

fetch:
	$(PY) -m pipeline fetch

records:
	$(PY) -m pipeline records

# Opus edits for chunks not yet in pipeline/fixes/ (resumable; OPUS_ARGS e.g. --words 8000 --limit 40 --dry-run).
opus-pass: records
	$(PY) -m pipeline opus-pass --parallel $(PARALLEL) $(OPUS_ARGS)

data: fetch records

site: records
	$(PY) -m pipeline site
	$(PAGEFIND) --site dist

# Semantic index into dist/semantic (run after site, which wipes dist). Per-talk vectors cache in build/embed-cache.
embed:
	uv run --extra embed python -m pipeline embed $(EMBED_ARGS)

serve:
	cd dist && $(PY) -m http.server $(PORT)

clean:
	rm -rf build dist
