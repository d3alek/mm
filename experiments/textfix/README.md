# Whisper transcript fixing: glossary vs LLM vs cascade

Bakeoff run on 2026-10-16 over the Medical Medium SRT corpus: 1,825 files, 12.52M words, 48.8k chunks of about 250 words each.

**Recommendation.** First, apply a glossary that Opus mined and reviewed (`results/glossary_C_opus.json`). The script is deterministic and auditable. Then run **Opus** on every chunk, returning minimal `{from,to}` edits (arm E). This beats every glossary-only or cascade variant by 14+ F1 points. Most real errors are ordinary words misheard in context, and no glossary or OOV trigger can see those. Haiku and Sonnet are clearly worse at the per-chunk step. Sonnet over-edits.

## Method

1. **Sample** (`scripts/sample.py`, seed 20261016). 150 chunks of consecutive SRT cues, about 250 words each:
   - `n*`: 50 random chunks, stratified across the 15 top-level folders.
   - `v*`: 50 chunks with a known variant, with quotas for the ambiguous ones: dolce/dulce ×8, lowercase "medical meeting", lycine, monolating, mullen, organite.
   - `x*`: 50 chunks with a rare out-of-vocabulary (OOV) token near a health word. OOV means not in hunspell en_US/en_GB plus the zxcvbn wiki/TV/name lists, and corpus count ≤20.
2. **Gold** (`prompts/gold_system.txt`). `claude-fable-5-1` at high effort labelled each chunk with `{from,to,reason,sure|uncertain}`. Fable is not a contestant. The result is 219 sure spans and 100 uncertain. Uncertain spans count neither for nor against an arm. An earlier Opus gold (`gold_opus.json`) is kept only for an agreement check.
3. **Arms.** Every LLM step ran on Haiku 5.5, Sonnet 5.5 and Opus 5.5 through `claude -p`, with tools disabled.
   - **A static.** The seed list from the prior scan, plus 166 entries mined mechanically by frequency and edit distance to domain jargon. A was frozen before any gold existed.
   - **B per chunk.** The model returns a JSON list of minimal edits and a script applies them. Each model ran twice.
   - **C hybrid.** The model mines glossary entries once from all 4,957 corpus OOV words, each with 3 contexts. Entries may carry context conditions. The seed entries go through the same review. The same model then reviews the list. `Cnr` is the same list without the review.
   - **D cascade.** Apply `glossary_C_opus`, then run the LLM only on chunks that still contain a suspicious token (OOV, count ≤20, not a glossary target).
   - **E.** The same cascade, with every chunk sent to Opus. This is D with "trigger = all".
4. **Scoring** (`scripts/score.py`). Matching is per occurrence. An arm edit that overlaps a sure gold span counts as TP only if the rendered window matches after normalising case and punctuation. Otherwise it is *harmW* (wrong target). An edit with no gold overlap is *harmC* (changed correct text).
   - `Rterm` is recall on names and jargon; `Rcomm` is recall on ordinary-word mishearings.
5. **Corpus audit** (`scripts/glossary_audit.py`). Fable judged 150 random corpus-wide firings of each glossary.

## Results (gold: Fable; all 150 chunks)

| arm | model | TP | harmC | harmW | FN | P | R | F1 | R term | R common |
|---|---|---|---|---|---|---|---|---|---|---|
| A0 seed list only | – | 73 | 0 | 3 | 146 | .961 | .333 | .495 | .41 | 0 |
| A static + mech. mining | – | 77 | 0 | 13 | 142 | .856 | .352 | .498 | .43 | 0 |
| B per chunk | haiku | 163 | 7 | 9 | 55 | .911 | .748 | .821 | .78 | .60 |
| B per chunk | sonnet | 162 | 25 | 7 | 56 | .835 | .743 | .786 | .75 | .70 |
| B per chunk | **opus** | **203** | 3 | 5 | **13** | .962 | **.940** | **.951** | .94 | .95 |
| C mined glossary | haiku | 107 | 0 | 7 | 112 | .939 | .489 | .643 | .60 | 0 |
| C mined glossary | sonnet | 101 | 0 | 4 | 118 | .962 | .461 | .623 | .56 | 0 |
| C mined glossary | **opus** | 117 | 0 | **0** | 101 | **1.000** | .537 | .699 | .66 | 0 |
| D cascade (C_opus + LLM on 11%) | haiku | 132 | 0 | 1 | 86 | .992 | .606 | .752 | .71 | .15 |
| D cascade | sonnet | 147 | 6 | 2 | 71 | .948 | .674 | .788 | .75 | .33 |
| D cascade | opus | 151 | 1 | 2 | 67 | .981 | .693 | .812 | .77 | .35 |
| E (C_opus + Opus on all) | opus | 204 | 4 | 6 | 12 | .953 | .944 | .949 | .94 | .95 |

- **Second run of B.** F1 was haiku .830, sonnet .808 and opus .953. Run-to-run Jaccard of the edit sets was haiku .57, sonnet .54 and opus .72. The number of chunks with identical edits was 108, 102 and 125 out of 150.
- **Random stratum.** This stratum is the closest to the real corpus. It has 26 sure errors in 50 chunks, about 2 per 1,000 words. Recall there:
  - B opus: .96
  - C opus: .19
  - D opus: .23
  - A: .12

  The glossary and the cascade mostly earn their recall in the variant-seeded stratum.
- **Corpus-wide glossary precision** (Fable judged 150 random firings of each glossary):

  | glossary | firings judged right | total corpus firings |
  |---|---|---|
  | C_opus | 149/149 (1 unsure) | 6,558 |
  | C_haiku | 143/146 | 6,509 |
  | A | 137/147 | 3,112 |

  The 10 wrong firings for A include `Vitamix→Vitamin`, `derealization→depersonalization`, `monolating→monolaurin`, and `Vimurji→Vimraji`, which maps one variant onto another.
- **Context-conditioned rules.**
  - Proposed: Haiku 137, Sonnet 85, Opus 69.
  - Accepted: Haiku 136, Sonnet 77, Opus 66.
  - On the sample they fired 1 to 4 times per glossary and caused **0** harmful edits. That is too few firings to call them proven.
- **Self-review is a rubber stamp.** Reviewers accepted 96% (Haiku), 96% (Sonnet) and 98% (Opus) of their own proposals. Review still mattered once. Opus corrected the seed `monolating→monolaurin` to `mono eating`. Haiku kept the wrong target and added `monolating cleanse→monocleanse cleanse`. Sonnet rejected the entry.
- **D cascade.**
  - LLM share: 5,632 of 49,630 corpus chunks (11.3%), or 1.43M of 12.5M words (11.5%).
  - Promotable residual edits (same from→to seen ≥2 times, or a term target): Haiku 10 of 20, Sonnet 20 of 43, Opus 20 of 39. Of those, 8, 12 and 16 match a gold TP. Examples are `Z-Lite→zeolite`, `L-Lycene→L-lysine`, `Methylmeme information→Medical Medium information` and `5-MTH→5-MTHF`.
- **Gold agreement.** Scored as an arm against Fable, the Opus gold gets P .985, R .895, F1 .938. This is the real labeller-bias measurement: against the Opus gold, B-opus scores F1 **.993**, against Fable .951. Haiku and Sonnet move by less than .03 between the two golds. A contestant scored by a gold from its own model is inflated.

## Where one arm beats another

1. **Ordinary words misheard in context** decide the result. Every glossary arm, A and C alike, has 0 recall here, and D gets 0.15 to 0.35. Examples:
   - "got rid of fat your ptsd" → *that*
   - "in the morning like my mother" → *light*
   - "it's all been flooding around" → *floating*

   Only a per-chunk LLM sees these, and Opus catches 95% of them.
2. **A seed entry was wrong.** `monolating` is "mono eating" (the Mono Eating Cleanse) in all 23 corpus hits ("monolating cleanse/cleanses/chapter"), for example "the monolating cleanse is a chance to recover". Static A turned all of them into *monolaurin* and inflated "monolaurin" search hits from 11 to 31 chunks. The per-chunk Opus and the Opus review got it right.
3. **Unseen spellings of known names.** A static list cannot follow variant chains:
   - "Vimor G zinc"
   - "bimber g company"
   - "Vimorgi spirulina"
   - "Methylmeme information"

   Mechanical mining also maps variants onto other variants, as in `Vimorgi→Vimrigi`. The LLM arms catch these.
4. **Where the glossary is better: consistency and auditability.** C_opus made 6,558 corpus edits with no judged errors, and the same input always gives the same output. B is non-deterministic: Opus agrees with itself on only 72% of edits. E keeps the glossary's corpus-wide consistency for known terms and adds the LLM for the rest.
5. **Weaker models over-edit or guess targets.** Sonnet "corrects" correct text 25 times. Examples:
   - "radical fats" → *rancid*
   - "a lysine" → *L-lysine*
   - "poachers" → *posers*

   Haiku picks wrong targets:
   - "mercer" → *Mercury*, where gold is MRSA
   - "problins" → *probiotics*, where gold is propolis
   - "sorolens" → *solanines*, where gold is psoralens

## Search impact (`results/search.json`, corpus chunks containing the canonical term)

| term | before | A | C_opus |
|---|---|---|---|
| Vimergy | 1,364 | 1,744 | **2,146 (+57%)** |
| cytomegalovirus | 252 | 269 | 317 (+26%) |
| streptococcus | 741 | 743 | 877 (+18%) |
| Medical Medium | 14,012 | 14,530 | 14,683 |
| monolaurin | 11 | 31 (false) | 12 |

## Cost and wall time (full corpus, list price)

These figures are measured through the CLI. Each call carries about 800 tokens of harness overhead, about 40% of the input, so a direct API call with prompt caching costs roughly 0.6× these numbers. Batch pricing is half the list price.

| step | Haiku | Sonnet | Opus |
|---|---|---|---|
| B or E: LLM on all 48.8k chunks | $21 ($10 batch), ~14 h at 6-way | $245 ($123), ~12 h | **$495 ($248)**, ~14 h |
| C: mine + review once | $0.30, ~7 min | $2.0, ~3 min | **$6.1**, ~6 min |
| D: residual on 5.6k chunks | $2.3 ($1.2) | $29 ($15) | $59 ($29) |

The Fable gold (150 chunks) used 309k tokens in and 39k out.

## Recommendation, per step

- **Rule mining: Opus.** It produced the only glossary with no wrong targets on the sample (C P = 1.000). It also had 149/149 right firings corpus-wide, and it fixed the bad seed entry. The once-off cost is about $6, so Opus wins. Ship `glossary_C_opus.json` as a versioned, diffable artifact.
- **Residual / per-chunk pass: Opus, on every chunk.** That is arm E, at about $250 batch, or less through the API with caching. Gating the LLM with an OOV trigger (arm D) saves about 89% of that cost but drops recall from .94 to .69. Most of the missed errors are in-lexicon mishearings, which no cheap trigger finds. Cost is secondary here, so run every chunk.
- Store the LLM edits as `{file, cue, from, to}` records, not rewritten text. Promote repeated term edits into the glossary, so later runs become more deterministic.

## Where the evidence is thin

- **Sample size.** There are 150 chunks and 219 sure gold spans, and only 26 of those spans are in the random stratum. Treat F1 differences under about 0.03, such as B-opus vs E-opus, as noise.
- **Labeller bias.** Fable, Opus, Sonnet and Haiku all come from one model family. The Opus-gold vs Fable-gold swing (.993 vs .951 for B-opus) shows that bias is real. Fable may still share blind spots with Opus.
- **Adjudication pending.** `adjudicate.csv` holds 60 of the 178 gold-vs-contestant disagreements:
  - 45 rows are gold edits that most arms missed.
  - 15 rows are contestant edits that gold rejected.

  The `alek` column is empty, so the scores are unadjudicated. Some gold items are arguably out of scope, such as `golden seal→goldenseal`, `15 to 32 minutes→30`, and song lyrics.
- **Strict scorer.** Some harmW counts are span-granularity artifacts, for example `I'll try general algae→trigeminal neuralgia` when the gold span is slightly different. These affect all LLM arms alike.
- **Context-conditioned rules** fired only 1 to 4 times in the sample. The corpus audit judged 150 firings per glossary, not all of them.

## Files

- `scripts/`: run each with `python3 -I`. The order is index_corpus, sample, mine_static, oov_contexts, make_jobs + run_llm, build_C, build_D, setup_E, make_gold, score, d_promote, cost, search, glossary_audit, adjudicate_sheet.
- `prompts/`: the gold, fix, mine and review prompts.
- `results/`:
  - `sample.jsonl`
  - `gold_fable.json` and `gold_opus.json`
  - `glossary_*.json`
  - `scores_gold_fable.json`: has per-edit rows
  - `table_gold_fable.txt`
  - `cost.json`, `search.json`, `audit_*_result.json`, `d_promote.json`, `D_trigger.json`
  - the raw CLI outputs for each step
- `data/`: copied wordlists (hunspell and zxcvbn).
