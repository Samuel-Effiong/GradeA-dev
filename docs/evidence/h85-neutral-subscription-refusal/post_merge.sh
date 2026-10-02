#!/bin/bash
# H-85 short gates after the base update onto the batch (H-88 merged).
set -u
TIP=b5911242; RED=85c71722
P=/home/bond-servant-in-training/Documents/Projects
WT=$P/Grade-Automator-Plus-h85-neutral-subscription-refusal
RP=$P/Grade-Automator-Plus-h78-repro
OUT=$P/GAP-d5-runs/h85
export EXEMPT_EMAIL_DOMAINS=
WRAP="systemd-inhibit --what=idle:sleep:handle-lid-switch --who=GAP --why=GAP-test-run --mode=block systemd-run --user --scope -q -p MemoryMax=6G -p MemorySwapMax=0 nice -n 10 timeout -k 60 1800"
say() { echo "$(date +%H:%M:%S) $*" | tee -a $OUT/post_merge.status; }
boundary() { [ ! -e $OUT/PAUSE ] || { say "PAUSED before $1"; exit 3; }; }
cd $WT || exit 9
[ "$(git rev-parse --short=8 HEAD)" = "$TIP" ] && [ -z "$(git status --porcelain --untracked-files=no)" ] || { say "ABORT: tree not frozen at $TIP"; exit 9; }

boundary repro
cd $RP || exit 9
git checkout -q --detach $RED || { say "ABORT: repro checkout"; exit 9; }
s=$(date +%s)
$WRAP python manage.py test billing.tests.test_neutral_subscription_refusal --settings=settings_worktree --noinput > $OUT/pm_repro_$RED.log 2>&1
rc=$?; echo "exit=$rc wall_s=$(( $(date +%s)-s ))" >> $OUT/pm_repro_$RED.log
say "repro exit=$rc (red expected)"; [ $rc -eq 1 ] || { say "STOP: repro was not a plain test failure"; exit 1; }

boundary "(a)"
cd $WT
s=$(date +%s)
$WRAP python manage.py test billing.tests.test_neutral_subscription_refusal billing.tests.test_other_school_before_subscription billing.tests.test_track_separation billing.tests.test_logs_carry_no_email assignments.tests_schema_extension users.tests_schema_extension $(cat $OUT/licence_modules.txt) --settings=settings_worktree --noinput > $OUT/pm_a_modules_$TIP.log 2>&1
rc=$?; echo "exit=$rc wall_s=$(( $(date +%s)-s ))" >> $OUT/pm_a_modules_$TIP.log
say "(a) exit=$rc"; [ $rc -eq 0 ] || { say "STOP: (a) red"; exit 1; }

boundary "(b)"
s=$(date +%s)
PYTHONDONTWRITEBYTECODE=1 $WRAP python docs/evidence/h85-neutral-subscription-refusal/run_mutants.py $TIP > $OUT/pm_b_mutation_battery_$TIP.log 2>&1
rc=$?; echo "exit=$rc wall_s=$(( $(date +%s)-s ))" >> $OUT/pm_b_mutation_battery_$TIP.log
say "(b) exit=$rc"
say "POST-MERGE GATES END"
