"""Seeded, reproducible sample of ~250-word chunks in three strata. Writes results/sample.jsonl."""
import collections
import json
import os
import random
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import CORPUS, RES, SEED_GLOSSARY, WORD_RE, parse_srt, srt_files, write_jsonl  # noqa: E402

SEED = 20261016
TARGET_WORDS = 250
rng = random.Random(SEED)

HEALTH = set("""liver thyroid celery juice juices supplement supplements zinc vitamin vitamins virus viruses
herb herbs herbal tea teas fruit fruits vegetable vegetables powder tincture tinctures b12 detox heavy metals
metal symptom symptoms illness disease gut brain blood adrenal adrenals mineral minerals iodine root leaf
leaves berries blueberries smoothie dose doses drops dropper dropperfuls bacteria strep fungus mold toxins
toxin food foods eat eating diet protein fat fats sugar glucose salt potassium magnesium sodium cleanse
kidney kidneys lymph immune nerve nerves inflammation eczema psoriasis acne medicine drug drugs doctor
doctors pills capsule capsules extract seaweed algae spirulina mushroom mushrooms oil oils pathogen
pathogens hormone hormones skin heal healing""".split())

files = srt_files()
chunks = []  # (file, cue_start, cue_end, text)
for p in files:
    cues = parse_srt(os.path.join(CORPUS, p))
    i = 0
    while i < len(cues):
        j, n = i, 0
        while j < len(cues) and n < TARGET_WORDS:
            n += len(cues[j].split())
            j += 1
        if n >= 120:  # drop tiny tail chunks
            chunks.append((p, i, j, " ".join(cues[i:j])))
        i = j
print("chunks", len(chunks))

folder = lambda p: p.split(os.sep)[0]
used = set()
sample = []

def take(idx, stratum, why):
    used.add(idx)
    p, a, b, t = chunks[idx]
    prefix = {"random": "n", "variant": "v", "rare": "x"}[stratum]
    sample.append({"id": f"{prefix}{len([s for s in sample if s['stratum']==stratum]):03d}",
                   "stratum": stratum, "why": why, "file": p, "cues": [a, b], "text": t,
                   "words": len(t.split())})

# --- stratum 2: known variants, with deliberate ambiguity quotas
quotas = [
    ("vimergy-variant", r"\bvim(?:rigy|rgy|mergy|pergy|bergy|urgy)\b|\bvivergy\b", 10),
    ("medical-meeting", r"\bMedical Meeting\b", 5),
    ("medical-meeting-lower", r"(?<!\bThe )\bmedical meeting\b", 3),
    ("medical-median", r"\bmedical median\b", 2),
    ("epstein-bar", r"\bEpstein bar\b", 5),
    ("cobalamin", r"\bmethicobalamin\b|\badenosilcobalamin\b", 4),
    ("monolating", r"\bmonolating\b", 3),
    ("lycine", r"\blycine\b", 4),
    ("dolce-dulce", r"\bdolce\b|\bdulce\b", 8),
    ("mullen", r"\bmullen\b", 4),
    ("organite", r"\borganite\b", 2),
]
for name, rx, q in quotas:
    r = re.compile(rx, re.I if name not in ("medical-meeting", "medical-meeting-lower") else 0)
    cands = [i for i, c in enumerate(chunks) if r.search(c[3]) and i not in used]
    rng.shuffle(cands)
    seen_files = set()
    picked = 0
    for i in cands:
        if picked >= q:
            break
        if chunks[i][0] in seen_files and len(cands) > q * 2:
            continue
        seen_files.add(chunks[i][0])
        take(i, "variant", name)
        picked += 1
    print(name, "cands", len(cands), "picked", picked)

# --- stratum 3: rare OOV tokens near health terms, no seed variants
wc = json.load(open(os.path.join(RES, "wordcounts.json")))
counts = wc["counts"]
oov = {}
with open(os.path.join(RES, "oov.tsv")) as fh:
    next(fh)
    for line in fh:
        w, n, d = line.rstrip("\n").split("\t")
        oov[w] = int(n)
seed_rx = re.compile("|".join(r"\b" + re.escape(f) + r"\b" for f, _ in SEED_GLOSSARY), re.I)
rare_hits = []  # (chunk idx, token)
for i, (p, a, b, t) in enumerate(chunks):
    if i in used or seed_rx.search(t):
        continue
    toks = [w.lower().strip("'") for w in WORD_RE.findall(t)]
    toks = [w[:-2] if w.endswith("'s") else w for w in toks]
    for k, w in enumerate(toks):
        if w in oov and oov[w] <= 20:
            window = toks[max(0, k - 10): k + 11]
            if any(x in HEALTH for x in window):
                rare_hits.append((i, w))
                break
rng.shuffle(rare_hits)
seen_tok, seen_file = set(), collections.Counter()
for i, w in rare_hits:
    if len([s for s in sample if s["stratum"] == "rare"]) >= 50:
        break
    if w in seen_tok or seen_file[chunks[i][0]] >= 1:
        continue
    seen_tok.add(w)
    seen_file[chunks[i][0]] += 1
    take(i, "rare", w)
print("rare candidates", len(rare_hits))

# --- stratum 1: random, stratified across top-level folders
by_folder = collections.defaultdict(list)
for i, c in enumerate(chunks):
    if i not in used:
        by_folder[folder(c[0])].append(i)
folders = sorted(by_folder)
per = {f: 50 // len(folders) for f in folders}
for f in rng.sample(folders, 50 - sum(per.values())):
    per[f] += 1
for f in folders:
    for i in rng.sample(by_folder[f], min(per[f], len(by_folder[f]))):
        take(i, "random", f)

write_jsonl(os.path.join(RES, "sample.jsonl"), sample)
print(collections.Counter(s["stratum"] for s in sample), "words", sum(s["words"] for s in sample))
json.dump({"chunks_total": len(chunks), "avg_words": sum(len(c[3].split()) for c in chunks) / len(chunks)},
          open(os.path.join(RES, "chunkstats.json"), "w"))
