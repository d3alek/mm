#!/bin/bash
# Runs every size/rep in priority order, one size at a time, 3-way parallel. Idempotent (cached outputs skip).
cd "$(dirname "$0")/.." || exit 1
for spec in 1000:r1 2000:r1 4000:r1 8000:r1 16000:r1 2000:r2 4000:r2 8000:r2 250x:r1 1000:r2 16000:r2; do
  n=${spec%%:*}; r=${spec##*:}
  echo "=== $n $r $(date +%T)"
  python3 -I scripts/run.py "results/jobs_$n.jsonl" "results/out_${n}_$r" 3 900
done
python3 -I scripts/run.py results/jobs_250E.jsonl results/out_250E_r2 3 900
echo "=== done $(date +%T)"
