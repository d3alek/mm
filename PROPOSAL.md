# mm: searchable Medical Medium transcripts — proposal

Status: proposal, 2026-10-16. Nothing built yet beyond the download in `~/code/mm/data/raw/` (gitignored).

## Source

- `https://downloads.miguelhx.com/MMTranscripts.zip` (61 MB, ETag-versioned, last updated 2026-10-11), from Miguel's [blog post](https://blog.miguelhx.com/mm-transcribe.html).
- 1,825 talks, each as TXT + SRT, ~12.5 M words, ~1,335 h, Whisper turbo output.

## Panel findings (four parallel reviews)

**Data**
- SRT text equals TXT in 1,797 / 1,825 pairs, so SRT is the single source.
- Dates parse from 66% of filenames (`yy-m-d`, but `m-d-yy` from 2026 on, TikTok `m-d-yyyy`); radio/podcast use episode numbers.
- 82 exact-duplicate groups (cross-posted talks): dedupe by text hash, keep `aliases`.
- No video IDs in the data: deep links need an offline match (YouTube/Rumble listings, date ±1 day + fuzzy title) kept in a reviewable `links.csv`.
- Whisper errors are large and systematic: "Medical Meeting" ×939, Vimergy misspelled ×~850, "Epstein bar" ×374, plus methylcobalamin, monolaurin, dulse, mullein, orgonite. Fix per token at ingest with a glossary, keep `text_raw`. Collapse repeated-cue hallucinations (84 files).

**Keyword search** (measured on the full corpus)

| Option | Index | First query | Verdict |
|---|---|---|---|
| Pagefind | 61 MB, 2.3k files | ~300 KB | Pick: static, phrase and filters, 30 s timestamp anchors; ranks per talk |
| SQLite FTS5 (sql.js-httpvfs) | 108 MB | 1–1.5 MB on common terms | Library unmaintained, slow on mobile |
| Worker + D1 FTS5 | same DB | ~5–30 KB JSON | Runner-up; becomes the pick if the site goes public (see Rights) |
| MiniSearch / lunr / Orama | 23 MB+ gz download | whole index | Too heavy for phones |

None of these tolerate typos except MiniSearch, so the glossary is required, not optional.

**Semantic search / Q&A**
- ~59k chunks of ~250 words (about 90 s), built from SRT cues. Each chunk keeps start/end seconds.
- `bge-small-en-v1.5` (384-d): embedding the corpus runs offline and costs $0; queries are embedded in the browser with transformers.js (34 MB, cached).
- Binary vectors take 2.8 MB, with a 22.6 MB int8 re-rank file. A scan takes about 5 ms.
- Hybrid search: BM25 and vector results are fused with RRF.
- Cloudflare Vectorize free tier is too small (5 M dims vs 22.7 M needed).
- Answer layer: v1 shows ranked passages with citations; v1.1 adds an optional bring-your-own-key Claude call from the browser.
- A local MCP server (`mm-search`) uses the same files, so Claude Code and Claude Desktop can query it.

**Rights**
- The blog gives no licence; the speech belongs to Anthony William / Medical Medium.
- The Healing Path is free but behind a sign-up (MM ships its own transcripts), and newsletter videos go to subscribers only. These two folders carry the most risk.
- Private GitHub Pages requires Enterprise Cloud; on any other plan GH Pages is public.
- Cloudflare Access is free for up to 50 users.
- Tiers:
  - (a) public full text: no.
  - (b) public search with snippets + links to the original, no Healing Path/newsletter text: acceptable.
  - (c) private: safest.
- Write to Miguel before anything public.

## Recommendation

1. **Pipeline** (`pipeline/`, Python): zip → normalized records (schema below) → glossary fix → dedupe → site build inputs. Daily GitHub Action does a conditional GET on the ETag and rebuilds only on change.
2. **v1 site** (Pagefind keyword search + 30 s timestamp anchors + source/series/year filters) on **Cloudflare Pages behind Access** (private, free). Not GitHub Pages: the index contains the full text, and GH Pages would make it public.
3. **v1.1**: hybrid semantic search (bge-small, binary vectors, RRF) + local MCP server + BYO-key "ask" button. Build a 30-question eval set first and choose chunking by measurement.
4. **Later, if public**: switch serving to Worker + D1 FTS5 returning snippets only (the full text never ships), drop the Healing Path/newsletter folders, and link out via `links.csv`. Only after contacting Miguel.

### Record schema

```json
{"id":"sha1(path)","content_sha":"…","platform":"facebook_youtube","series":"…","variant":"youtube|instagram|audio|video|null",
 "title":"…","title_raw":"…","date":"2021-10-15","date_precision":"day|year|null","date_source":"ymd|mdy|folder|episode",
 "episode":null,"part":2,"path":"…","aliases":["…"],
 "video":{"provider":"youtube","id":null,"match":"title_date|manual|none"},"duration_s":3712,
 "flags":["repeat_collapsed","short"],"cues":[{"t":12.34,"e":17.9,"text":"…"}]}
```

## Open decisions for Alek

1. Audience: just me / a few people (private, Cloudflare Access) or public (snippets-only, Worker + D1)?
2. v1 scope: keyword search only, or include semantic + MCP straight away?
3. Video deep links: worth the matching step in v1, or show timestamps only?
