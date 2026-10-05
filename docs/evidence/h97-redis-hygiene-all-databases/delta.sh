#!/bin/bash
# H-97 delta (rule 15.4, SM ruling): the touched module + mutant D5.
set -u
TIP=f1e0e9d7
P=/home/bond-servant-in-training/Documents/Projects
WT=$P/Grade-Automator-Plus-h97-redis-hygiene-all-databases
OUT=$P/GAP-d5-runs/h97
export EXEMPT_EMAIL_DOMAINS=
WRAP="systemd-inhibit --what=idle:sleep:handle-lid-switch --who=GAP --why=GAP-test-run --mode=block systemd-run --user --scope -q -p MemoryMax=6G -p MemorySwapMax=0 nice -n 10 timeout -k 60 1800"
say() { echo "$(date +%H:%M:%S) $*" | tee -a $OUT/delta.status; }
cd $WT || exit 9
[ "$(git rev-parse --short=8 HEAD)" = "$TIP" ] && [ -z "$(git status --porcelain --untracked-files=no)" ] || { say "ABORT: tree not frozen at $TIP"; exit 9; }
s=$(date +%s)
$WRAP python manage.py test AutoGrader.tests_redis_hygiene_databases AutoGrader.tests_cache_invalidation_coverage --settings=settings_worktree --noinput > $OUT/delta_modules_$TIP.log 2>&1
rc=$?; echo "exit=$rc wall_s=$(( $(date +%s)-s ))" >> $OUT/delta_modules_$TIP.log
say "delta modules exit=$rc"; [ $rc -eq 0 ] || { say "STOP: red"; exit 1; }
s=$(date +%s)
PYTHONDONTWRITEBYTECODE=1 $WRAP python docs/evidence/h97-redis-hygiene-all-databases/run_mutants.py $TIP --only D5 > $OUT/delta_mutant_D5_$TIP.log 2>&1
rc=$?; echo "exit=$rc wall_s=$(( $(date +%s)-s ))" >> $OUT/delta_mutant_D5_$TIP.log
say "delta mutant D5 exit=$rc"
say "DELTA END"
