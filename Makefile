# MM_RAW: dir holding MMTranscripts.zip (+ etag.txt). SITE_BASE: URL path prefix.
MM_RAW ?= data/raw
SITE_BASE ?= /
PAGEFIND ?= npx -y pagefind@1.5.2
PY ?= python3
PORT ?= 8000
export MM_RAW SITE_BASE CF_BEACON_TOKEN

.PHONY: fetch records data site serve clean

fetch:
	$(PY) -m pipeline fetch

records:
	$(PY) -m pipeline records

data: fetch records

site: records
	$(PY) -m pipeline site
	$(PAGEFIND) --site dist

serve:
	cd dist && $(PY) -m http.server $(PORT)

clean:
	rm -rf build dist
