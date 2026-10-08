#!/bin/bash
# H-97 gate (test infrastructure): the red commit fails; the two hygiene modules are
# green under --parallel 4, three times; the mutants; the 9 guards once.
set -u
TIP=a4f379be; RED=5d8af9f4
P=/home/bond-servant-in-training/Documents/Projects
WT=$P/Grade-Automator-Plus-h97-redis-hygiene-all-databases
RP=$P/Grade-Automator-Plus-h78-repro
OUT=$P/GAP-d5-runs/h97
LOCK=/home/bond-servant-in-training/.machine-fullsuite.lock
export EXEMPT_EMAIL_DOMAINS=
PRE="systemd-inhibit --what=idle:sleep:handle-lid-switch --who=GAP --why=GAP-test-run --mode=block systemd-run --user --scope -q -p MemoryMax=6G -p MemorySwapMax=0 nice -n 10"
WRAP="$PRE timeout -k 60 1800"
say() { echo "$(date +%H:%M:%S) $*" | tee -a $OUT/chain.status; }
boundary() { [ ! -e $OUT/PAUSE ] || { say "PAUSED before $1"; exit 3; }; }
cd $WT || exit 9
[ "$(git rev-parse --short=8 HEAD)" = "$TIP" ] && [ -z "$(git status --porcelain)" ] || { say "ABORT: tree not frozen at $TIP"; exit 9; }

cd $RP || exit 9
git checkout -q --detach $RED || { say "ABORT: repro checkout"; exit 9; }
s=$(date +%s)
$WRAP python manage.py test AutoGrader.tests_redis_hygiene_databases --settings=settings_worktree --noinput > $OUT/repro_$RED.log 2>&1
rc=$?; echo "exit=$rc wall_s=$(( $(date +%s)-s ))" >> $OUT/repro_$RED.log
say "repro exit=$rc (red expected)"
[ $rc -eq 1 ] || { say "STOP: repro was not a plain test failure"; exit 1; }

cd $WT
# The first run with the fix sweeps databases 0-15: list before and after it.
python $OUT/listing.py "before the first run with the fix" > $OUT/listing_before.txt 2>&1
{ date +%H:%M:%S; pgrep -af "manage.py test" | grep -v pgrep | cut -c1-220; } > $OUT/other_runs_at_first_run.txt 2>&1
for n in 1 2 3; do
  boundary "parallel run $n"
  s=$(date +%s)
  $PRE flock $LOCK timeout -k 60 1800 python manage.py test AutoGrader.tests_redis_hygiene_databases AutoGrader.tests_redis_hygiene AutoGrader.tests_redis_test_isolation --settings=settings_worktree --noinput --parallel 4 > $OUT/module_parallel4_run${n}_$TIP.log 2>&1
  rc=$?; echo "exit=$rc wall_s=$(( $(date +%s)-s ))" >> $OUT/module_parallel4_run${n}_$TIP.log
  say "module --parallel 4 run $n exit=$rc"; [ $rc -eq 0 ] || { say "STOP: run $n red"; exit 1; }
  if [ $n -eq 1 ]; then
    python $OUT/listing.py "after the first run with the fix" > $OUT/listing_after.txt 2>&1
    python $OUT/compare.py $OUT/listing_before.txt $OUT/listing_after.txt > $OUT/listing_compare.txt 2>&1 || { say "STOP: the after listing is not as expected (listing_compare.txt)"; exit 1; }
    say "listings: $(tail -1 $OUT/listing_compare.txt)"
  fi
done

boundary "mutants"
s=$(date +%s)
PYTHONDONTWRITEBYTECODE=1 $WRAP python docs/evidence/h97-redis-hygiene-all-databases/run_mutants.py $TIP > $OUT/b_mutation_battery_$TIP.log 2>&1
rc=$?; echo "exit=$rc wall_s=$(( $(date +%s)-s ))" >> $OUT/b_mutation_battery_$TIP.log
say "mutants exit=$rc"; [ $rc -eq 0 ] || { say "STOP: mutants not all killed"; exit 1; }

boundary "guards"
s=$(date +%s)
$WRAP python manage.py test AutoGrader.tests_beat_locks AutoGrader.tests_management_commands_are_commands billing.tests.test_logs_carry_no_email AutoGrader.tests_no_wildcard_invalidation AutoGrader.tests_cache_invalidation_coverage AutoGrader.tests_migration_rollback_defaults AutoGrader.tests_redis_test_isolation AutoGrader.tests_beat_health classrooms.tests_teacher_access_sweep classrooms.tests_course_roster_scope_sweep assignments.tests_schema_extension users.tests_schema_extension --settings=settings_worktree --noinput > $OUT/guards_$TIP.log 2>&1
rc=$?; echo "exit=$rc wall_s=$(( $(date +%s)-s ))" >> $OUT/guards_$TIP.log
say "9 guards exit=$rc"
say "CHAIN END"
