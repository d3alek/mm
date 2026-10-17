// Home page search. "Words" = Pagefind keyword search; "Meaning" = Pagefind fused (RRF) with
// bge-small vector search over ~90 s passages (index in semantic/, query embedded in the browser).
const TRANSFORMERS = "https://cdn.jsdelivr.net/npm/@huggingface/transformers@4.2.0/dist/transformers.min.js";
const SECTION_S = 30;
const KW_PAGES = 8; // Pagefind talks per batch; each costs one fragment fetch (the full talk text), the slowest step
const CANDIDATES = 500; // Hamming survivors re-ranked with int8 vectors
const SEM_KEEP = 150; // re-ranked passages entering the fusion
const RRF_K = 60;
const PAGE_TALKS = 10;
const PASSAGES_PER_TALK = 3;
const SNIPPET_WORDS = 40;
const STOP = new Set("about after again also because been before being does doing from have having here into just more most much only other over same should some such than that their them then there these they this those through very what when where which while will with would your".split(" "));

const base = new URL("../", import.meta.url).pathname;
const $ = (id) => document.getElementById(id);
const q = $("q"), fp = $("fp"), fs = $("fs"), fy = $("fy");
const hint = $("hint"), status = $("status"), list = $("results"), more = $("more");
const modeButtons = Array.from(document.querySelectorAll("[data-mode]"));
const params = new URLSearchParams(location.search);
const HINTS = {
  meaning: "Finds passages about what you ask, even in other words, mixed with exact word matches. Each result links to its moment in the talk.",
  words: "Exact words. Phrases in quotes match exactly. Results link to the 30-second section where the words occur.",
};

let mode = params.get("mode") === "words" ? "words" : "meaning";
q.value = params.get("q") || "";
fp.value = params.get("platform") || "";
fs.value = params.get("series") || "";
fy.value = params.get("year") || "";

// ---------- Pagefind ----------
let pagefindP;
function pagefind() {
  pagefindP ??= import(base + "pagefind/pagefind.js").then(async (pf) => {
    await pf.options({ baseUrl: base });
    pf.init();
    return pf;
  });
  return pagefindP;
}

function filters() {
  const f = {};
  if (fp.value) f.platform = fp.value;
  if (fs.value) f.series = fs.value;
  if (fy.value) f.year = fy.value;
  return f;
}

function parseUrl(url) {
  const m = /\/t\/([0-9a-f]+)\/(?:#t(\d+))?/.exec(url);
  return m && { talk: m[1], sec: m[2] == null ? null : +m[2] };
}

// One Pagefind query, paged: next() returns the passages of the next KW_PAGES talks.
async function keywordSearch(query) {
  const pf = await pagefind();
  const res = await pf.search(query, { filters: filters() });
  let offset = 0;
  return {
    total: res.results.length,
    async next() {
      const pages = await Promise.all(res.results.slice(offset, offset + KW_PAGES).map((r) => r.data()));
      offset += KW_PAGES;
      const out = [];
      for (const p of pages) {
        const where = parseUrl(p.url);
        if (!where) continue;
        const info = { title: p.meta.title, date: p.meta.date || "", series: (p.filters.series || [])[0] || "" };
        const subs = (p.sub_results || [])
          .map((s) => ({ s, at: parseUrl(s.url) }))
          .filter((x) => x.at && x.at.sec != null)
          .sort((a, b) => b.s.locations.length - a.s.locations.length)
          .slice(0, PASSAGES_PER_TALK);
        if (!subs.length) out.push({ talk: where.talk, start: null, end: null, excerpt: p.excerpt, info });
        for (const { s, at } of subs) out.push({ talk: where.talk, start: at.sec, end: at.sec + SECTION_S, excerpt: s.excerpt, info });
      }
      return out;
    },
    get done() { return offset >= res.results.length; },
  };
}

// ---------- Semantic index ----------
let metaP, indexP, modelP;
const meta = () => (metaP ??= fetch(base + "semantic/meta.json").then((r) => (r.ok ? r.json() : null)).catch(() => null));

function index() {
  indexP ??= meta().then(async (m) => {
    const get = (name) => fetch(`${base}semantic/${name}?v=${m.v}`).then((r) => {
      if (!r.ok) throw new Error(`${name}: HTTP ${r.status}`);
      return r.arrayBuffer();
    });
    const [chunks, bin, i8] = await Promise.all([get("chunks.bin"), get("bin.bin"), get("i8.bin")]);
    const n = m.n;
    const talkIndex = new Map(m.talks.map((t, i) => [t[0], i]));
    return {
      m, n, talkIndex,
      talk: new Uint16Array(chunks, 0, n),
      start: new Uint16Array(chunks, 2 * n, n),
      end: new Uint16Array(chunks, 4 * n, n),
      bin: new Uint32Array(bin),
      i8: new Int8Array(i8),
    };
  });
  return indexP;
}

function model(onProgress) {
  modelP ??= meta().then(async (m) => {
    const { pipeline, env } = await import(TRANSFORMERS);
    env.allowLocalModels = false;
    return pipeline("feature-extraction", m.model, { dtype: "q8", revision: m.revision, progress_callback: onProgress });
  });
  return modelP;
}

function popcount(x) {
  x -= (x >>> 1) & 0x55555555;
  x = (x & 0x33333333) + ((x >>> 2) & 0x33333333);
  return (Math.imul((x + (x >>> 4)) & 0x0f0f0f0f, 0x01010101) >>> 24);
}

// Hamming scan over sign bits, then cosine re-rank of the closest CANDIDATES with int8 rows.
function vectorSearch(ix, qv) {
  const dims = ix.m.dims, words = dims / 32, n = ix.n;
  const qb = new Uint8Array(dims / 8);
  for (let d = 0; d < dims; d++) if (qv[d] > 0) qb[d >> 3] |= 128 >> (d & 7);
  const q32 = new Uint32Array(qb.buffer);
  const f = filters();
  const allowed = ix.m.talks.map((t) => (!f.platform || t[3] === f.platform) && (!f.series || t[4] === f.series) && (!f.year || t[5] === f.year));
  const dist = new Uint16Array(n);
  const hist = new Uint32Array(dims + 2);
  for (let i = 0; i < n; i++) {
    if (!allowed[ix.talk[i]]) { dist[i] = dims + 1; continue; }
    let h = 0;
    for (let w = 0, o = i * words; w < words; w++) h += popcount(ix.bin[o + w] ^ q32[w]);
    dist[i] = h;
    hist[h]++;
  }
  let cut = 0, seen = 0;
  while (cut <= dims && seen + hist[cut] < CANDIDATES) seen += hist[cut++];
  const cand = [];
  for (let i = 0; i < n && cand.length < CANDIDATES; i++) if (dist[i] < cut) cand.push(i);
  for (let i = 0; i < n && cand.length < CANDIDATES && cut <= dims; i++) if (dist[i] === cut) cand.push(i);
  const scored = cand.map((i) => {
    let dot = 0, norm = 0;
    for (let d = 0, o = i * dims; d < dims; d++) {
      const v = ix.i8[o + d];
      dot += v * qv[d];
      norm += v * v;
    }
    return { i, score: dot / Math.sqrt(norm) };
  });
  scored.sort((a, b) => b.score - a.score);
  return scored.slice(0, SEM_KEEP).map(({ i, score }) => {
    const t = ix.m.talks[ix.talk[i]];
    return { talk: t[0], start: ix.start[i], end: ix.end[i], score };
  });
}

// Reciprocal-rank fusion at talk+time level: a keyword section inside a semantic passage boosts that passage.
function fuse(sem, kw) {
  const items = sem.map((s, r) => ({ ...s, rrf: 1 / (RRF_K + r + 1) }));
  kw.forEach((k, r) => {
    const add = 1 / (RRF_K + r + 1);
    const hit = k.start != null && items.find((s) => s.talk === k.talk && !s.kw && s.start != null && k.start < s.end && k.end > s.start);
    if (hit) Object.assign(hit, { rrf: hit.rrf + add, kw: true, excerpt: k.excerpt, excerptAt: k.start, info: k.info });
    else items.push({ ...k, kw: true, rrf: add });
  });
  return items.sort((a, b) => b.rrf - a.rrf);
}

// Ranked passages → talks (best first), dropping passages that overlap one already kept.
function groupByTalk(items) {
  const talks = new Map();
  for (const it of items) {
    let g = talks.get(it.talk);
    if (!g) talks.set(it.talk, (g = { talk: it.talk, info: it.info, passages: [] }));
    g.info ??= it.info;
    if (g.passages.length >= PASSAGES_PER_TALK) continue;
    const s = it.start ?? 0, e = it.end ?? 0;
    if (g.passages.some((p) => (p.start ?? 0) < e && (p.end ?? 0) > s)) continue;
    g.passages.push(it);
  }
  return Array.from(talks.values());
}

// ---------- Snippets from talk pages ----------
const pages = new Map();
function sections(talk) {
  if (!pages.has(talk)) {
    pages.set(talk, fetch(`${base}t/${talk}/`).then((r) => r.text()).then((html) => {
      const doc = new DOMParser().parseFromString(html, "text/html");
      return Array.from(doc.querySelectorAll("main h3[id^=t]")).map((h) => ({
        sec: +h.id.slice(1),
        text: h.nextElementSibling ? h.nextElementSibling.textContent : "",
      }));
    }));
  }
  return pages.get(talk);
}

function terms(query) {
  return query.toLowerCase().match(/[a-z0-9']+/g)?.filter((w) => w.length > 2 && !STOP.has(w)) ?? [];
}

function stem(w) {
  return w.replace(/[^a-z0-9]/g, "").replace(/(ing|ed|es|s)$/, "");
}

// The SNIPPET_WORDS-word window of the passage with the most query words, query words marked.
// Returns the section second of the window's first query word, so links land where the words are.
async function fillSnippet(el, p, query) {
  const secs = await sections(p.talk);
  const from = Math.floor(p.start / SECTION_S) * SECTION_S;
  const words = [], wordSec = [];
  for (const s of secs) {
    if (s.sec < from || s.sec >= p.end) continue;
    for (const w of s.text.split(/\s+/).filter(Boolean)) { words.push(w); wordSec.push(s.sec); }
  }
  const want = new Set(terms(query).map(stem));
  const hitAt = words.map((w) => (want.has(stem(w.toLowerCase())) ? 1 : 0));
  let best = 0, bestScore = -1, cur = 0;
  for (let i = 0; i < words.length; i++) {
    cur += hitAt[i] - (i >= SNIPPET_WORDS ? hitAt[i - SNIPPET_WORDS] : 0);
    if (i >= SNIPPET_WORDS - 1 && cur > bestScore) { bestScore = cur; best = i - SNIPPET_WORDS + 1; }
  }
  const slice = words.slice(best, best + SNIPPET_WORDS);
  el.textContent = best > 0 ? "… " : "";
  slice.forEach((w, i) => {
    if (want.has(stem(w.toLowerCase()))) {
      const m = document.createElement("mark");
      m.textContent = w;
      el.append(m);
    } else el.append(w);
    if (i < slice.length - 1) el.append(" ");
  });
  if (best + SNIPPET_WORDS < words.length) el.append(" …");
  const first = hitAt.indexOf(1, best);
  return wordSec[first >= 0 && first < best + SNIPPET_WORDS ? first : best];
}

// ---------- Rendering ----------
const AT_TIME = {
  youtube: (url, s) => `${url}&t=${s}s`,
  rumble: (url, s) => `${url}?start=${s}`,
  podcast: (url, s) => `${url}#t=${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`,
};

function clock(sec) {
  const h = Math.floor(sec / 3600), m = Math.floor(sec / 60) % 60, s = sec % 60;
  const mm = h ? String(m).padStart(2, "0") : String(m);
  return (h ? h + ":" : "") + mm + ":" + String(s).padStart(2, "0");
}

function el(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  Object.assign(e, attrs);
  e.append(...kids);
  return e;
}

function renderTalk(g, query, talkMeta) {
  const t = talkMeta && talkMeta.get(g.talk);
  const title = t ? t[1] : g.info?.title || g.talk;
  const sub = t ? [t[2] || "Undated", t[4]] : [g.info?.date || "Undated", g.info?.series].filter(Boolean);
  const li = el("li", { className: "hit" },
    el("a", { className: "hit-title", href: `${base}t/${g.talk}/` }, title),
    el("small", {}, sub.join(" · ")));
  const ul = el("ul", { className: "passages" });
  for (const p of g.passages) {
    const row = el("li");
    let pointAt = () => {};
    if (p.start != null) {
      const ts = row.appendChild(el("a", { className: "ts" }));
      const watch = t && t[6] && AT_TIME[t[6]]
        ? row.appendChild(el("a", { className: "watch", target: "_blank", rel: "noopener", title: "Watch from here" }, "▶"))
        : null;
      pointAt = (section) => {
        const at = Math.max(section, p.start);
        ts.href = `${base}t/${g.talk}/#t${section}`;
        ts.textContent = clock(at);
        if (watch) watch.href = AT_TIME[t[6]](t[7], at);
      };
      pointAt(p.excerptAt ?? Math.floor(p.start / SECTION_S) * SECTION_S);
    }
    const snip = el("span", { className: "snip" });
    if (p.excerpt) snip.innerHTML = p.excerpt; // Pagefind's excerpt: escaped text + <mark>
    else fillSnippet(snip, p, query).then((section) => section != null && pointAt(section)).catch(() => {});
    row.append(snip);
    ul.append(row);
  }
  li.append(ul);
  return li;
}

// ---------- Controller ----------
let run = 0;
let shown = [], showMore = null;

function setUrl() {
  const url = new URL(location.href);
  [["q", q.value.trim()], ["mode", mode === "words" ? "words" : ""], ["platform", fp.value], ["series", fs.value], ["year", fy.value]]
    .forEach(([k, v]) => (v ? url.searchParams.set(k, v) : url.searchParams.delete(k)));
  history.replaceState(null, "", url);
}

function syncSeries() {
  Array.from(fs.options).forEach((o) => { o.hidden = !!(fp.value && o.value && o.dataset.p !== fp.value); });
  if (fs.value && fs.selectedOptions[0].hidden) fs.value = "";
}

async function talkMeta() {
  const m = await meta();
  return m && new Map(m.talks.map((t) => [t[0], t]));
}

function paint(groups, tm, query, total) {
  list.replaceChildren(...groups.map((g) => renderTalk(g, query, tm)));
  status.textContent = total;
}

async function search() {
  const id = ++run;
  const query = q.value.trim();
  setUrl();
  more.hidden = true;
  showMore = null;
  if (!query) { list.replaceChildren(); status.textContent = ""; return; }
  status.textContent = "Searching…";
  const tm = await talkMeta();
  const live = () => id === run;
  const kwP = keywordSearch(query);

  if (mode === "words" || !tm) {
    const kw = await kwP;
    let items = await kw.next();
    if (!live()) return;
    const draw = () => paint(groupByTalk(items), tm, query, `${kw.total} talks contain these words`);
    draw();
    showMore = async () => { items = items.concat(await kw.next()); if (live()) { draw(); more.hidden = kw.done; } };
    more.hidden = kw.done;
    return;
  }

  // Meaning: keyword hits right away, then the fused list once the model and index are in.
  const kwItems = kwP.then((kw) => kw.next());
  let loading = !(await Promise.race([modelP, Promise.resolve(false)]));
  if (loading) {
    status.textContent = "Loading the meaning model (about 30 MB, once; cached after)…";
    kwItems.then((items) => {
      if (live() && loading) {
        list.replaceChildren(...groupByTalk(items).slice(0, PAGE_TALKS).map((g) => renderTalk(g, query, tm)));
      }
    });
  }
  const progress = {};
  const onProgress = (e) => {
    if (e.status === "progress" && e.total > 1e6) {
      progress[e.file] = e;
      const loaded = Object.values(progress).reduce((a, p) => a + p.loaded, 0);
      const total = Object.values(progress).reduce((a, p) => a + p.total, 0);
      if (live() && loading) status.textContent = `Loading the meaning model, ${Math.round((100 * loaded) / total)}% of ${Math.round(total / 1e6)} MB (once; cached after)…`;
    }
  };
  let ex, ix;
  try {
    [ex, ix] = await Promise.all([model(onProgress), index()]);
  } catch (err) {
    console.warn("semantic search unavailable", err);
    if (!live()) return;
    mode = "words";
    paintMode();
    return search();
  }
  loading = false;
  const out = await ex(ix.m.query_prefix + query, { pooling: "cls", normalize: true });
  const sem = vectorSearch(ix, out.data);
  const kw = await kwItems;
  if (!live()) return;
  const groups = groupByTalk(fuse(sem, kw));
  let n = PAGE_TALKS;
  const draw = () => paint(groups.slice(0, n), tm, query, `${groups.length} talks match`);
  draw();
  showMore = () => { n += PAGE_TALKS; draw(); more.hidden = n >= groups.length; };
  more.hidden = n >= groups.length;
}

function paintMode() {
  modeButtons.forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.mode === mode)));
  hint.textContent = HINTS[mode];
}

let timer;
function later() { clearTimeout(timer); timer = setTimeout(search, 250); }

q.addEventListener("input", later);
q.addEventListener("keydown", (e) => { if (e.key === "Enter") { clearTimeout(timer); search(); } });
[fp, fs, fy].forEach((s) => s.addEventListener("input", () => { syncSeries(); search(); }));
more.addEventListener("click", () => showMore && showMore());
modeButtons.forEach((b) => b.addEventListener("click", () => {
  if (mode === b.dataset.mode) return;
  mode = b.dataset.mode;
  paintMode();
  search();
}));

syncSeries();
paintMode();
meta().then((m) => {
  if (!m) { // no semantic index in this build: words only
    mode = "words";
    paintMode();
    modeButtons[0].parentElement.hidden = true;
  }
  if (q.value) search();
  else q.focus();
});
