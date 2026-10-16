# Opus text-fix: how big can a chunk be?

Run on 2026-10-16 on top of the textfix bakeoff (`../README.md`). Question: the production Opus pass (arm E) sends ~250-word chunks, so 46k calls at ~5 s fixed CLI overhead each. How large can a chunk get before fix quality drops?

**Answer.** On this sample, no tested size breaks the bar. Every size from 1,000 to 16,000 words keeps F1 within 0.012 of the 250-word baseline, and no size shows a late-window recall collapse. **The largest acceptable size is 16,000 words.** In practice that means a whole talk, because the median window at that size was 10.2k words. **Recommendation: 8,000 words.** The full remaining run drops from 38k calls to about 2.1k, from about 20 h to about 2 h at 3-way, and from about $450 to about $160. The cost is a small recall-for-precision trade: about −2 recall points and +1.5 precision points. Going from 8k to 16k saves only another 20 minutes, and 16k is the only size where the recall loss is significant.

## Method

1. **Gold.** There are two golds:
   - **Fable.** `../results/gold_fable_final.json`.
   - **Adjudicated (headline).** Fable plus Alek's 57 verdicts from `../verdicts_alek.json` on `../adjudicate.csv` rows (`scripts/adjudicated_gold.py`):
     - 44 verdicts keep the gold.
     - 11 "model" verdicts each add one contestant edit as a sure item.
     - 2 "custom" verdicts replace gold's `to`. For v044, the custom text drops a trailing `'s` that the original span carries, and the script keeps it.
     - 3 csv rows have no verdict and stay as Fable labelled them.

   Chunk n021 (sung lyrics) is excluded everywhere. That leaves 149 gold chunks: 225 sure spans (adjudicated) and 214 (Fable).
2. **Windows** (`scripts/build_windows.py`).
   - For each gold chunk and size N, the script takes a window of whole SRT cues of about N words from the same talk.
   - Placement uses a seeded, balanced cycle of thirds. The chunk starts at u·(N − chunk words), where u is drawn in the first, middle or last third equally often. Talk boundaries can override this, and the real position is recorded.
   - A gold chunk that already lies inside an earlier window of the same talk reuses that window (10 talks hold 2 gold chunks).
   - Talks shorter than N become one whole-talk window. This applies to the talks of 45 gold chunks at 8k and 139 at 16k.
3. **Call** (`scripts/run.py`). The call follows arm E and production exactly:
   - glossary `glossary_C_opus` first (byte-identical to `pipeline/glossary.json`)
   - the production prompt (asserted equal to `pipeline/fix_prompt.txt`)
   - `claude -p --model claude-opus-5-5 --tools ""`, with no effort flag
   - at most 3 calls at once, each started only while `MemAvailable` ≥ 3 GB

   An unparseable answer is kept as-is, with no retry.
4. **Score** (`scripts/score_cs.py`).
   - Edits are applied with production semantics: `from` is replaced at every occurrence, longest first.
   - The original window is word-diffed against the final window. Diff spans that lie inside a gold chunk are scored with `score.py`'s matching rules, copied unchanged.
   - Opus edits outside the gold chunks give an edits-per-1,000-words signal.
   - The **250 baseline is arm E itself** (`../results/E_all_opus`), scored by the same code, plus one fresh E rerun (`250_r2`) to measure baseline noise.
   - **250x** is the 1,000-word windows re-cut into production 250-word chunks. It is the same text as the 1000 run, so it is a clean comparator for the edit rate.
5. **Reps.** Every size ran twice (r1, r2). The r2 calls hit the prompt cache, because the same prompts went out within the hour, so **cost per call is taken from r1**. The model's answers are unaffected.
6. **Position** (`scripts/aggregate.py`). Recall in each third of the window is compared with arm E's recall on **the same gold items**. A raw per-third recall mixes position with which chunks happened to land in that third.
7. **Projection** (`scripts/project.py`). This step reads `build/records` and `pipeline/fixes` read-only:
   - 7,878 of 45,950 production chunks are done, which leaves 9.60M words.
   - Undone runs are re-cut at N with the production rule.
   - Seconds and dollars come from a line a + b·words fitted on r1 calls: 5.3 s + 1.0 s per 1k words, and $0.0097 + $0.0146 per 1k words.
   - The 250 row uses the production log's measured rates instead: $0.0118 per chunk, and 1.04 s per chunk wall at 6-way.

## Results

**Pooled over both runs, adjudicated gold unless marked.** "Late-third R" is recall on gold items whose chunk sits in the last third of its window. The number after "vs" is arm E's recall on exactly those items. "Edits/1k outside" counts Opus edits outside the gold chunks.

| size | F1 adj (r1; r2) | F1 Fable | P adj | R adj | late-third R vs E, same items (n) | edits/1k outside | s/call | tokens in/out per call | $/call |
|---|---|---|---|---|---|---|---|---|---|
| 250 (arm E) | **.911** (.915; .906) | .946 | .941 | .882 | – | – | 6.1 | 1,934 / 105 | 0.010 |
| 250x (1000 windows re-cut) | .908 (1 run) | .946 | .943 | .876 | – | 2.52 | 5.3 | 1,925 / 105 | 0.012 |
| 1000 | .899 (.897; .901) | .936 | .933 | .867 | .857 vs .879 (70) | 2.32 | 6.4 | 3,228 / 256 | 0.026 |
| 2000 | .911 (.924; .897) | .944 | .941 | .882 | .838 vs .845 (74) | 2.09 | 7.3 | 4,884 / 389 | 0.041 |
| 4000 | .902 (.912; .893) | .938 | .949 | .860 | .880 vs .889 (104) | 1.91 | 9.4 | 7,904 / 623 | 0.068 |
| 8000 | .908 (.916; .900) | .944 | .956 | .864 | .830 vs .840 (103) | 1.84 | 12.4 | 13,217 / 974 | 0.114 |
| 16000 (median 10.2k) | .902 (.904; .900) | .940 | .951 | .858 | .797 vs .812 (96) | 1.78 | 14.5 | 17,973 / 1,250 | 0.141 |

**Paired bootstrap against the 250 baseline** (both runs on each side, over gold chunks, 95% CI):

| size | ΔR | ΔP |
|---|---|---|
| 1000 | −.015 [−.035, +.005] | −.007 [−.025, +.007] |
| 2000 | .000 [−.017, +.018] | .000 [−.019, +.022] |
| 4000 | −.022 [−.048, +.002] | +.008 [−.010, +.026] |
| 8000 | −.018 [−.038, +.002] | +.015 [−.004, +.035] |
| 16000 | −.025 [−.047, −.004] | +.010 [−.011, +.033] |

**Full remaining run** (9.6M words):

| size | calls | wall at 3-way | wall at 6-way | list cost |
|---|---|---|---|---|
| 250 | 38,072 | ~22 h | ~11 h | ~$450 |
| 1000 | 10,317 | 5.9 h | 3.0 h | $240 |
| 2000 | 5,561 | 3.6 h | 1.8 h | $194 |
| 4000 | 3,251 | 2.5 h | 1.2 h | $172 |
| **8000** | **2,125** | **1.9 h** | **1.0 h** | **$161** |
| 16000 | 1,495 | 1.6 h | 0.8 h | $155 |

The 250 row uses production-log rates. The other rows use the fitted line.

### Reliability and truncation

- Across all 2,186 calls there were 0 failures, 0 unparseable answers and 0 truncations: every call ended with `end_turn`. The longest call took 36 s, on a 16k window.
- Run-to-run Jaccard of in-chunk edits was .89 to .93 at every size, including 250 (.91). Large windows are not less stable.

## Reading it

- **F1 is flat.** Every size is within .012 of the baseline's pooled .911. Arm E's own two runs differ by .009, and single-run F1 at 2000 swings by .027 between reps.
- **No long-context collapse.**
  - The late-third recall gap against E on the same items is −.007 to −.022 at every size, and it does not grow with N.
  - The 250x control, which has no position effect because it is chunked, shows the same −.022 on the same items as the 1000 run.
  - The gap is therefore noise, not position.
  - The per-third numbers in the first and middle thirds look alike.
- **There is a small, consistent shift as windows grow: fewer edits.**
  - From 4,000 words up, Opus makes about 10–15% fewer edits, both inside gold chunks (2.84–3.0 per 1k at 250, 2.5–2.7 at ≥4k) and outside them. Outside, the rate falls from 2.52 per 1k (250x) through 2.32 (1000) to 1.8 (8k–16k).
  - In the scores this shows up as about −2 recall points (roughly 4–5 more missed errors out of 225) and +1 to +1.5 precision points.
  - The recall drop is significant only at 16k. At 4k and 8k its CI just touches zero.
- **The knee.** Quality is unchanged through 2,000 words, the last size with exactly zero measured ΔR. The slight under-editing starts at 4,000. Cost and time flatten after 4,000 to 8,000: going from 4k to 8k saves 0.6 h and $11, and going from 8k to 16k saves 0.3 h and $6.
- **Acceptable**, defined as pooled F1 within 0.02 of the baseline and no late-window collapse: every size passes. The largest acceptable size is **16,000**, which is effectively a whole talk. The recommended size is **8,000**, because the 16k step buys almost nothing and is the one size with a significant recall loss. A smaller failure blast radius also matters when a usage limit or retry hits. If recall matters more than the last hour of wall time, use **2,000**: it shows no measured change and still takes 3.6 h at 3-way instead of ~22 h.

## Caveats

- **Sample size.** There are 149 chunks and 225 sure spans. Differences under about 0.02 to 0.03 in F1, and under 0.03 in any per-third recall (each third holds 50 to 100 items), are within noise. Two reps per size narrow this, but they do not remove it.
- **The sample is enriched.** The variant and rare strata are error-heavy. The random stratum, which is closest to the corpus, holds only 26 errors, so the result there is not separately resolvable.
- **Context differs.** Arm E saw each gold chunk cut at fixed edges, while the windows give it context on both sides. The test does not separate "more context helps" from "longer input hurts". Their net effect is what is measured.
- **The `from`-replaces-every-occurrence rule.** In a 10k-word window an over-general `from` can hit more places than in 250 words. Any such damage inside gold chunks is already counted in P. Outside the chunks it is not judged, only counted in the edit rate, which went down.
- **Projection.** This is a linear fit on these calls. Real throughput also depends on usage limits. One 16k call is about 2.3× the work of an 8k retry.
- **Same model family as the labeller.** Fable's blind spots may be shared, but that affects every size equally.

## Files

- `scripts/`
  - `build_windows.py`: windows and jobs
  - `glossary.py`: the arm-E glossary pass
  - `run.py`: CLI runner with the memory gate
  - `run_all.sh`: the run order
  - `adjudicated_gold.py`
  - `score_cs.py`: writes `results/summary.json`, `results/table.md` and `results/rows.json`
  - `aggregate.py`: pooled results and the position control, written to `results/aggregate.json`
  - `project.py`: writes `results/projection.json`

  Run everything with `python3 -I` from this directory.
- `results/`: raw CLI outputs `out_<size>_r<n>/`, windows, jobs, the adjudicated gold and per-edit rows. These are gitignored because they carry transcript text. Only the numeric `summary.json`, `table.md`, `aggregate.json` and `projection.json` are tracked.
