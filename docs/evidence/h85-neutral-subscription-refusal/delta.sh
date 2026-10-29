#!/bin/bash
# H-85 delta after 1a's P1/P2/Z1c (rule 15.4): the touched modules + mutant N8.
set -u
TIP=29bebdcb
P=/home/bond-servant-in-training/Documents/Projects
WT=$P/Grade-Automator-Plus-h85-neutral-subscription-refusal
OUT=$P/GAP-d5-runs/h85
export EXEMPT_EMAIL_DOMAINS=
WRAP="systemd-inhibit --what=idle:sleep:handle-lid-switch --who=GAP --why=GAP-test-run --mode=block systemd-run --user --scope -q -p MemoryMax=6G -p MemorySwapMax=0 nice -n 10 timeout -k 60 1800"
say() { echo "$(date +%H:%M:%S) $*" | tee -a $OUT/delta.status; }
cd $WT || exit 9
[ "$(git rev-parse --short=8 HEAD)" = "$TIP" ] && [ -z "$(git status --porcelain --untracked-files=no)" ] || { say "ABORT: tree not frozen at $TIP"; exit 9; }
s=$(date +%s)
$WRAP python manage.py test billing.tests.test_neutral_subscription_refusal billing.tests.test_other_school_before_subscription assignments.tests_schema_extension users.tests_schema_extension --settings=settings_worktree --noinput > $OUT/delta_modules_$TIP.log 2>&1
rc=$?; echo "exit=$rc wall_s=$(( $(date +%s)-s ))" >> $OUT/delta_modules_$TIP.log
say "delta modules exit=$rc"; [ $rc -eq 0 ] || { say "STOP: red"; exit 1; }
s=$(date +%s)
PYTHONDONTWRITEBYTECODE=1 $WRAP python docs/evidence/h85-neutral-subscription-refusal/run_mutants.py $TIP --only N6,N8 > $OUT/delta_mutants_$TIP.log 2>&1
rc=$?; echo "exit=$rc wall_s=$(( $(date +%s)-s ))" >> $OUT/delta_mutants_$TIP.log
say "delta mutants N6,N8 exit=$rc"
say "DELTA END"
