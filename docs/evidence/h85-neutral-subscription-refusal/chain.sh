#!/bin/bash
# H-85 gate chain on the frozen tip. Stops on the first unexpected result.
# Usage: chain.sh <tip-sha8> <red-test-commit-sha8>
set -u
TIP=$1; RED=$2
P=/home/bond-servant-in-training/Documents/Projects
WT=$P/Grade-Automator-Plus-h85-neutral-subscription-refusal
RP=$P/Grade-Automator-Plus-h78-repro
OUT=$P/GAP-d5-runs/h85
LOCK=/home/bond-servant-in-training/.machine-fullsuite.lock
export EXEMPT_EMAIL_DOMAINS=
PRE="systemd-inhibit --what=idle:sleep:handle-lid-switch --who=GAP --why=GAP-test-run --mode=block systemd-run --user --scope -q -p MemoryMax=6G -p MemorySwapMax=0 nice -n 10"
WRAP="$PRE timeout -k 60 1800"
stamp() { while IFS= read -r l; do printf '%s %s\n' "$(date +%H:%M:%S)" "$l"; done; }
say() { echo "$(date +%H:%M:%S) $*" | tee -a $OUT/chain.status; }

cd $WT || exit 9
[ "$(git rev-parse --short=8 HEAD)" = "$TIP" ] && [ -z "$(git status --porcelain)" ] || { say "ABORT: tree not frozen at $TIP"; exit 9; }

# repro: the test commit itself (the new tests over the unfixed code)
cd $RP || exit 9
git checkout -q --detach $RED || { say "ABORT: repro checkout"; exit 9; }
s=$(date +%s)
$WRAP python manage.py test billing.tests.test_neutral_subscription_refusal --settings=settings_worktree --noinput > $OUT/repro_$RED.log 2>&1
rc=$?; echo "exit=$rc wall_s=$(( $(date +%s)-s ))" >> $OUT/repro_$RED.log
say "repro exit=$rc (red expected)"
[ $rc -eq 1 ] || { say "STOP: repro was not a plain test failure"; exit 1; }

cd $WT
s=$(date +%s)
$WRAP python manage.py test billing.tests.test_neutral_subscription_refusal billing.tests.test_other_school_before_subscription billing.tests.test_track_separation billing.tests.test_license_service billing.tests.test_add_teachers_other_school_not_disclosed billing.tests.test_logs_carry_no_email billing.tests.test_h38_teacher_removal --settings=settings_worktree --noinput > $OUT/a_modules_$TIP.log 2>&1
rc=$?; echo "exit=$rc wall_s=$(( $(date +%s)-s ))" >> $OUT/a_modules_$TIP.log
say "(a) exit=$rc"; [ $rc -eq 0 ] || { say "STOP: (a) red"; exit 1; }

s=$(date +%s)
PYTHONDONTWRITEBYTECODE=1 $WRAP python docs/evidence/h85-neutral-subscription-refusal/run_mutants.py $TIP > $OUT/b_mutation_battery_$TIP.log 2>&1
rc=$?; echo "exit=$rc wall_s=$(( $(date +%s)-s ))" >> $OUT/b_mutation_battery_$TIP.log
say "(b) exit=$rc"
[ -z "$(git status --porcelain --untracked-files=no)" ] || { say "STOP: tracked files changed after (b)"; exit 1; }
[ $rc -eq 0 ] || { say "STOP: (b) not all killed"; exit 1; }

# (c) is a parallel run: under the machine's full-suite lock
s=$(date +%s)
$PRE flock $LOCK timeout -k 60 1800 python manage.py test billing AutoGrader.tests_no_wildcard_invalidation AutoGrader.tests_cache_invalidation_coverage AutoGrader.tests_migration_rollback_defaults AutoGrader.tests_redis_test_isolation AutoGrader.tests_beat_health classrooms.tests_teacher_access_sweep classrooms.tests_course_roster_scope_sweep assignments.tests_schema_extension users.tests_schema_extension --settings=settings_worktree --noinput --parallel 2 --verbosity 2 2>&1 | stamp > $OUT/c_app_billing_guards_$TIP.log
rc=${PIPESTATUS[0]}; echo "exit=$rc wall_s=$(( $(date +%s)-s ))" >> $OUT/c_app_billing_guards_$TIP.log
say "(c) exit=$rc"
say "CHAIN END"
