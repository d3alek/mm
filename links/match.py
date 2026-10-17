#!/usr/bin/env python3
"""Match Medical Medium transcripts (.srt) to their online originals.

Usage: python3 -I match.py [--refresh] [--srt-root DIR]

1. fetch   listings into cache/ (skipped when the cache file exists, unless --refresh)
           - YouTube channel tabs videos/streams/shorts (yt-dlp --flat-playlist)
           - Rumble channel pages (embedded JSON, paged until no new items)
           - SoundCloud RSS feed of the Medical Medium Podcast (+ radio show archive)
2. match   each transcript against all listings: fuzzy title, date (+-2 d), duration,
           podcast episode number, ids embedded in filenames
3. write   links.csv (exact|high|medium) and review.csv (low, plus demoted duplicates)

Deterministic: same cache + same transcripts => same CSVs.

Deep links to second S:  youtube  {url}&t={S}s
                         rumble   {url}?start={S}   (share-dialog form; not verified in the player)
                         podcast  {url}#t={M}:{SS}  (SoundCloud "share at time" fragment)
Radio Shows transcripts all end near 55:00 while SoundCloud cuts vary, so radio timestamps can
drift by duration_delta_s; Instagram copies of simulcast streams are a different recording.
"""
import csv, datetime as dt, json, os, re, subprocess, sys, time, unicodedata
import xml.etree.ElementTree as ET
from difflib import SequenceMatcher

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, 'cache')
SRT_ROOT = '/home/alek/code/mm/data/raw/extracted/TranscriptsSrt'
YT_CHANNEL = 'https://www.youtube.com/channel/UCUORv_qpgmg8N5plVqlYjXg'  # @MedicalMediumAnthonyWilliam
RUMBLE_CHANNEL = 'https://rumble.com/c/MedicalMedium'
PODCAST_RSS = 'https://feeds.soundcloud.com/users/soundcloud:users:184711729/sounds.rss'
UA = 'Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0'


# ---------------------------------------------------------------- fetch
def cached(name, refresh, producer):
    path = os.path.join(CACHE, name)
    if refresh or not os.path.exists(path) or os.path.getsize(path) < 100:
        data = producer()
        with open(path, 'wb') as f:
            f.write(data)
        time.sleep(1)
    with open(path, 'rb') as f:
        return f.read()


def run(cmd):
    return subprocess.run(cmd, check=True, capture_output=True, timeout=900).stdout


def fetch_all(refresh):
    os.makedirs(CACHE, exist_ok=True)
    items = []
    for tab in ('videos', 'streams', 'shorts'):
        raw = cached(f'yt_{tab}.json', refresh, lambda tab=tab: run(
            ['yt-dlp', '--no-update', '--flat-playlist', '-J', f'{YT_CHANNEL}/{tab}']))
        for e in json.loads(raw)['entries']:
            items.append(dict(provider='youtube', kind='radio' if 'Radio Show Archive' in e['title'] else tab, id=e['id'], title=e['title'],
                              date=None, duration=e.get('duration'), episode=None,
                              url=f'https://www.youtube.com/watch?v={e["id"]}'))
    seen = set()
    for tab, page in ((tab, page) for tab in ('videos', 'livestreams') for page in range(1, 200)):
        if page > 1 and not new:
            continue
        raw = cached(f'rumble_{tab}_p{page}.html', refresh, lambda tab=tab, page=page: run(
            ['curl', '-sL', '-A', UA, f'{RUMBLE_CHANNEL}/{tab}?page={page}']))
        new = 0
        for v in rumble_videos(raw.decode('utf-8', 'replace')):
            if v['permalink_id'] in seen:
                continue
            seen.add(v['permalink_id']); new += 1
            d = v.get('upload_date') or v.get('live_datetime')
            items.append(dict(provider='rumble', kind='rumble', id=v['permalink_id'],
                              title=v['title'], duration=v.get('duration'), episode=None,
                              date=dt.date.fromisoformat(d[:10]) if d else None,
                              url=v['url'].split('?')[0]))
    raw = cached('podcast_soundcloud.rss', refresh, lambda: run(['curl', '-sL', '-A', UA, PODCAST_RSS]))
    ns = {'itunes': 'http://www.itunes.com/dtds/podcast-1.0.dtd'}
    for it in ET.fromstring(raw).iter('item'):
        title = it.findtext('title').strip()
        m = re.match(r'^(\d{3})\s', title)
        pub = dt.datetime.strptime(it.findtext('pubDate')[5:16], '%d %b %Y').date()
        hms = [int(x) for x in (it.findtext('itunes:duration', namespaces=ns) or '0').split(':')]
        secs = sum(x * 60 ** i for i, x in enumerate(reversed(hms)))
        tid = it.findtext('guid').rsplit('/', 1)[-1]
        items.append(dict(provider='podcast', kind='radio' if 'Radio Show Archive' in title else 'podcast',
                          id=tid, title=title, date=pub, duration=secs or None,
                          episode=int(m[1]) if m else None, url=it.findtext('link').strip()))
    return items


def rumble_videos(html):
    """Yield the video objects embedded as JSON in a Rumble channel page."""
    dec = json.JSONDecoder()
    for m in re.finditer(r'\{"object_type":"video"', html):
        try:
            obj, _ = dec.raw_decode(html, m.start())
        except ValueError:
            continue
        if obj.get('by', {}).get('url', '').rstrip('/').endswith('/c/MedicalMedium') and obj.get('permalink_id'):
            yield obj


# ---------------------------------------------------------------- transcripts
def mkdate(y, m, d):
    try:
        return dt.date(y if y > 100 else 2000 + y, m, d)
    except ValueError:
        return None


def parse_name(rel):
    name = os.path.basename(rel)[:-4]
    parts = rel.split('/')
    fy = next((int(x) for x in parts if re.fullmatch(r'20\d\d', x)), None)
    ids = {}
    m = re.search(r'\[(v[a-z0-9]{5,7})\]', name)
    if m:
        ids['rumble'] = m[1]; name = name.replace(m[0], '')
    m = re.search(r'-([A-Za-z0-9_-]{11})$', name)
    if m and re.search(r'[A-Z]', m[1]) and re.search(r'\d', m[1]):
        ids['youtube'] = m[1]; name = name[:m.start()]
    date = None
    m = re.match(r'^(\d{4})-(\d{1,2})-(\d{1,2})\b', name)
    if m:
        date = mkdate(int(m[1]), int(m[2]), int(m[3])); name = name[m.end():]
    else:
        m = re.match(r'^(\d{1,2})-(\d{1,2})-(\d{1,2})\b', name)
        if m:
            a, b, c = map(int, m.groups())
            ymd = mkdate(a, b, c) if a >= 14 else None
            mdy = mkdate(c, a, b) if c >= 14 else None
            if ymd and mdy and fy:
                date = ymd if ymd.year == fy else mdy
            elif ymd and mdy:
                date = ymd if ymd.year < 2026 else mdy
            else:
                date = ymd or mdy
            name = name[m.end():]
        else:
            m = re.search(r'(\d{1,2})-(\d{1,2})-(20\d\d)', name)
            if m:
                date = mkdate(int(m[3]), int(m[1]), int(m[2]))
    episode = None
    m = re.match(r'^(\d{3})\s', name)
    if m and 'Podcast' in rel or (m and 'Meditations' in rel):
        episode = int(m[1]); name = name[m.end():]
    name = re.sub(r'\(\d\)\s*$', '', name)
    return dict(title=name.strip(' -'), date=date, year=fy or (date.year if date else None),
                episode=episode, ids=ids, truncated=len(os.path.basename(rel)[:-4]) in range(30, 35))


def srt_duration(path):
    last = None
    with open(path, encoding='utf-8', errors='replace') as f:
        for line in f:
            if '-->' in line:
                last = line
    if not last:
        return None
    m = re.search(r'-->\s*(\d+):(\d+):(\d+)[,.](\d+)', last)
    return int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3]) + int(m[4]) / 1000 if m else None


# ---------------------------------------------------------------- similarity
NOISE = re.compile(r'\b(medical medium|radio show archive|season \d+ episode #?\d+|anthony william|'
                   r'yt|ig|youtube|instagram|facebook|livestream|live|mm)\b')
STOP = set('a an the and or of to in on for with is are you your my it its this that be at by from '
           'what why how do does'.split())


def norm(s):
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode().lower()
    s = s.replace('&', ' and ').replace("'", '')
    s = re.sub(r'[^a-z0-9#]+', ' ', s)
    s = NOISE.sub(' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def tokens(s):
    return {t for t in s.split() if t not in STOP and len(t) > 1}


IDF = {}


def build_idf(titles):
    import math
    df = {}
    for t in titles:
        for w in tokens(t):
            df[w] = df.get(w, 0) + 1
    n = len(titles)
    IDF.update({w: math.log((n + 1) / (c + 0.5)) for w, c in df.items()})


def weighted_dice(ta, tb):
    w = lambda ts: sum(IDF.get(x, 8.0) for x in ts)
    den = w(ta) + w(tb)
    return 2 * w(ta & tb) / den if den else 0.0


def title_sim(a, b, truncated):
    """Mean of character similarity and rarity-weighted token overlap, in [0, 1]."""
    if not a or not b:
        return 0.0
    seq = SequenceMatcher(None, a, b, autojunk=False).ratio()
    tok = weighted_dice(tokens(a), tokens(b))
    if truncated and len(a) >= 18 and len(b) > len(a):
        bp = b[:len(a)]
        seq = max(seq, SequenceMatcher(None, a, bp, autojunk=False).ratio())
        ta, tb = tokens(a.rsplit(' ', 1)[0]), tokens(bp.rsplit(' ', 1)[0])
        tok = max(tok, weighted_dice(ta, tb))
    return (seq + tok) / 2


# ---------------------------------------------------------------- matching
PREFERRED = [('Podcast Docuseries', 'podcast'), ('Radio Shows', 'podcast'), ('Meditations', 'podcast'),
             ('Rumble', 'rumble')]


def preferred_provider(rel):
    for prefix, prov in PREFERRED:
        if rel.startswith(prefix):
            return prov
    return 'youtube'


NUMBERED = re.compile(r'\b(part|pt|day|night|round|episode|ep|challenge)\s*#?\s*(\d+)\b')


def numbers(ntitle):
    """{'part': 2, 'day': 3, ...}: sequence markers that must agree between two titles."""
    out = {}
    for kind, n in NUMBERED.findall(ntitle):
        kind = {'pt': 'part', 'ep': 'episode'}.get(kind, kind)
        out.setdefault(kind, int(n))
    return out


MONTHS = 'jan feb mar apr may jun jul aug sep oct nov dec'.split()


def title_month_day(title):
    m = re.search(r'\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.? (\d{1,2})(st|nd|rd|th)\b', title.lower())
    return (MONTHS.index(m[1]) + 1, int(m[2])) if m else None


def evaluate(t, it):
    sim = title_sim(t['ntitle'], it['ntitle'], t['truncated'])
    nt, ni = t['nums'], it['nums']
    if any(k in ni and ni[k] != v for k, v in nt.items()):
        return dict(score=0, sim=sim, conf=None, delta=None, dd=None)
    if ('part' in nt) != ('part' in ni) and max(nt.get('part', 1), ni.get('part', 1)) > 1:
        return dict(score=0, sim=sim, conf=None, delta=None, dd=None)
    if it['md'] and t['date'] and abs((dt.date(2000, *it['md']) - dt.date(2000, t['date'].month, t['date'].day)).days) > 2:
        return dict(score=0, sim=sim, conf=None, delta=None, dd=None)
    ep = t['episode'] is not None and it['episode'] == t['episode']
    dd = abs((it['date'] - t['date']).days) if it['date'] and t['date'] else None
    delta = round(abs(t['duration'] - it['duration']), 1) if it['duration'] and t['duration'] else None
    dur_ok = delta is not None and delta <= max(20, 0.02 * it['duration'])
    # radio archive episodes all run ~55 min, so duration says nothing about which one it is
    dur_tight = delta is not None and delta <= 10 and it['kind'] != 'radio'
    dur_ok = dur_ok and it['kind'] != 'radio'
    dur_close = delta is not None and delta <= max(60, 0.08 * it['duration'])
    dur_bad = delta is not None and not dur_close and it['kind'] != 'radio'
    date_ok = (dd is not None and dd <= 2) or bool(it['md'] and t['date'])
    date_bad = dd is not None and dd > 7
    year_bad = it['date'] is not None and t['year'] and abs(it['date'].year - t['year']) > 1
    score = sim + (0.35 if ep else 0) + (0.25 if dur_ok else 0.1 if dur_close else -0.25 if dur_bad else 0) \
        + (0.2 if date_ok else -0.3 if date_bad else 0) + (0.05 if it['provider'] == t['pref'] else 0)
    if ep and (sim >= 0.5 or dur_ok):
        conf = 'high'
    elif sim >= 0.85 and (dur_ok or date_ok) and not dur_bad and not date_bad:
        conf = 'high'
    elif sim >= 0.6 and dur_ok and date_ok:
        conf = 'high'
    elif sim >= 0.97 and len(t['tok']) >= 2 and (dur_ok or delta is None or it['kind'] == 'radio') \
            and not date_bad and not year_bad:
        conf = 'high'
    elif sim >= 0.85 and not dur_bad and not date_bad and not year_bad:
        conf = 'medium'
    elif sim >= 0.55 and (dur_tight or date_ok) and not dur_bad and not date_bad:
        conf = 'medium'
    elif sim >= 0.5 and delta is not None and delta <= 3 and it['duration'] >= 900 and it['kind'] != 'radio' \
            and not date_bad and not year_bad:
        conf = 'medium'
    elif sim >= 0.45 or (dur_ok and sim >= 0.25):
        conf = 'low'
    else:
        conf = None
    # a different cut of the same session (e.g. the Instagram copy of a simulcast): right video,
    # but timestamps drift, so it is never `high`
    if conf == 'high' and delta is not None and delta > 60 and it['kind'] != 'radio':
        conf = 'medium'
    # radio archive uploads only answer for the Radio Shows folder
    if it['kind'] == 'radio' and not t['path'].startswith('Radio Shows') and conf in ('high', 'medium'):
        conf = 'low'
    return dict(score=round(score, 3), sim=sim, conf=conf, delta=delta, dd=dd)


RANK = {'exact': 4, 'high': 3, 'medium': 2, 'low': 1, None: 0}


def match(transcripts, items):
    by_id = {(it['provider'], it['id']): it for it in items}
    for it in items:
        it['ntitle'] = norm(re.sub(r'^\d{3}\s', '', it['title']))
        it['tok'] = tokens(it['ntitle']); it['nums'] = numbers(it['ntitle']); it['md'] = title_month_day(it['title'])
    build_idf([it['ntitle'] for it in items] + [t['ntitle'] for t in transcripts])
    results = {}
    for t in transcripts:
        best = None
        for prov, vid in t['ids'].items():
            it = by_id.get((prov, vid))
            if it:
                best = (it, dict(score=9.0, sim=1.0, conf='exact', dd=None,
                                 delta=round(abs(t['duration'] - it['duration']), 1)
                                 if it['duration'] and t['duration'] else None))
                break
            # id in filename but not in the listing: still a valid link
            url = f'https://www.youtube.com/watch?v={vid}' if prov == 'youtube' else f'https://rumble.com/{vid}'
            best = (dict(provider=prov, id=vid, url=url, title='', date=None),
                    dict(score=9.0, sim=1.0, conf='exact', dd=None, delta=None))
        if best is None:
            cands = []
            for it in items:
                if not (t['tok'] & it['tok']) and not (it['episode'] is not None and it['episode'] == t['episode']) \
                        and not (it['duration'] and t['duration'] and abs(it['duration'] - t['duration']) <= 3):
                    continue
                ev = evaluate(t, it)
                if ev['conf']:
                    cands.append((RANK[ev['conf']], ev['score'], it['provider'] == t['pref'], it['id'], it, ev))
            # same confidence: the folder's own platform wins (a FB/YT livestream is usually
            # mirrored on Rumble, whose dated listing would otherwise score higher)
            cands.sort(key=lambda c: (-c[0], not c[2], -c[1], c[3]))
            if cands:
                best = (cands[0][4], cands[0][5])
                # ambiguity: a same-provider runner-up with the same confidence and nearly the same score
                rival = next((c for c in cands[1:] if c[4]['provider'] == best[0]['provider']), None)
                if rival and rival[0] == cands[0][0] and cands[0][1] - rival[1] < 0.03 \
                        and best[1]['conf'] in ('high', 'medium'):
                    best[1]['conf'] = 'medium' if best[1]['conf'] == 'high' else 'low'
        results[t['path']] = best
    # one listing item claimed by several transcripts: keep near-duration twins (duplicate
    # transcripts of one video, e.g. a challenge folder copy), demote the rest to review
    tdur = {t['path']: t['duration'] for t in transcripts}
    claims = {}
    for p, b in results.items():
        if b and b[1]['conf'] in ('high', 'medium') and b[1]['conf'] != 'exact':
            claims.setdefault((b[0]['provider'], b[0]['id']), []).append(p)
    for key, paths in claims.items():
        if len(paths) < 2:
            continue
        paths.sort(key=lambda p: (-RANK[results[p][1]['conf']], -results[p][1]['score'], p))
        top = results[paths[0]][1]
        for p in paths[1:]:
            ev = results[p][1]
            twin = (ev['delta'] is not None and top['delta'] is not None and ev['delta'] <= 20 and top['delta'] <= 20) \
                or (tdur[p] and tdur[paths[0]] and abs(tdur[p] - tdur[paths[0]]) <= 2)  # same recording
            if not twin:
                ev['conf'] = 'low'; ev['note'] = 'listing item claimed by ' + paths[0]
    return results


def main():
    refresh = '--refresh' in sys.argv
    root = sys.argv[sys.argv.index('--srt-root') + 1] if '--srt-root' in sys.argv else SRT_ROOT
    items = fetch_all(refresh)
    transcripts = []
    for d, _, fs in os.walk(root):
        for f in fs:
            if f.endswith('.srt'):
                rel = os.path.relpath(os.path.join(d, f), root)
                t = parse_name(rel)
                t.update(path=rel, duration=srt_duration(os.path.join(d, f)), pref=preferred_provider(rel))
                t['ntitle'] = norm(t['title']); t['tok'] = tokens(t['ntitle']); t['nums'] = numbers(t['ntitle'])
                transcripts.append(t)
    transcripts.sort(key=lambda t: t['path'])
    results = match(transcripts, items)
    cols = ['path', 'provider', 'video_id', 'url', 'match', 'score', 'listing_title', 'listing_date',
            'duration_delta_s']
    with open(os.path.join(HERE, 'links.csv'), 'w', newline='') as fa, \
            open(os.path.join(HERE, 'review.csv'), 'w', newline='') as fb:
        wa, wb = csv.writer(fa), csv.writer(fb)
        wa.writerow(cols); wb.writerow(cols + ['transcript_date', 'transcript_duration_s', 'note'])
        for t in transcripts:
            b = results[t['path']]
            if not b:
                continue
            it, ev = b
            row = [t['path'], it['provider'], it['id'], it['url'], ev['conf'], ev['score'], it['title'],
                   it['date'].isoformat() if it.get('date') else '',
                   '' if ev['delta'] is None else ev['delta']]
            if ev['conf'] in ('exact', 'high', 'medium'):
                wa.writerow(row)
            else:
                wb.writerow(row + [t['date'] or '', round(t['duration'] or 0), ev.get('note', '')])
    # coverage summary
    cov = {}
    for t in transcripts:
        top = t['path'].split('/')[0]
        b = results[t['path']]
        c = cov.setdefault(top, dict(total=0, exact=0, high=0, medium=0, low=0))
        c['total'] += 1
        if b:
            c[b[1]['conf']] += 1
    print(f'listing items: ' + ', '.join(f'{p}={sum(1 for i in items if i["provider"] == p)}'
                                         for p in ('youtube', 'rumble', 'podcast')))
    print(f'{"folder":28} total exact high medium low')
    for k in sorted(cov):
        c = cov[k]
        print(f'{k:28} {c["total"]:5} {c["exact"]:5} {c["high"]:4} {c["medium"]:6} {c["low"]:3}')


if __name__ == '__main__':
    main()
