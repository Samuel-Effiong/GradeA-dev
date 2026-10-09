#!/bin/bash
# H-94 gate (test-only): the red commit's two tests fail; the fixed module is
# green under --parallel 4, five times; the 9 guards once.
set -u
TIP=2d04fcaf; RED=ae773cb9
P=/home/bond-servant-in-training/Documents/Projects
WT=$P/Grade-Automator-Plus-h94-redis-hygiene-test-race
RP=$P/Grade-Automator-Plus-h78-repro
OUT=$P/GAP-d5-runs/h94
LOCK=/home/bond-servant-in-training/.machine-fullsuite.lock
export EXEMPT_EMAIL_DOMAINS=
PRE="systemd-inhibit --what=idle:sleep:handle-lid-switch --who=GAP --why=GAP-test-run --mode=block systemd-run --user --scope -q -p MemoryMax=6G -p MemorySwapMax=0 nice -n 10"
WRAP="$PRE timeout -k 60 1800"
say() { echo "$(date +%H:%M:%S) $*" | tee -a $OUT/chain.status; }
cd $WT || exit 9
[ "$(git rev-parse --short=8 HEAD)" = "$TIP" ] && [ -z "$(git status --porcelain)" ] || { say "ABORT: tree not frozen at $TIP"; exit 9; }

cd $RP || exit 9
git checkout -q --detach $RED || { say "ABORT: repro checkout"; exit 9; }
s=$(date +%s)
$WRAP python manage.py test AutoGrader.tests_redis_hygiene --settings=settings_worktree --noinput > $OUT/repro_$RED.log 2>&1
rc=$?; echo "exit=$rc wall_s=$(( $(date +%s)-s ))" >> $OUT/repro_$RED.log
say "repro exit=$rc (red expected)"
[ $rc -eq 1 ] || { say "STOP: repro was not a plain test failure"; exit 1; }

cd $WT
for n in 1 2 3 4 5; do
  s=$(date +%s)
  $PRE flock $LOCK timeout -k 60 1800 python manage.py test AutoGrader.tests_redis_hygiene --settings=settings_worktree --noinput --parallel 4 > $OUT/module_parallel4_run${n}_$TIP.log 2>&1
  rc=$?; echo "exit=$rc wall_s=$(( $(date +%s)-s ))" >> $OUT/module_parallel4_run${n}_$TIP.log
  say "module --parallel 4 run $n exit=$rc"; [ $rc -eq 0 ] || { say "STOP: run $n red"; exit 1; }
done

s=$(date +%s)
$WRAP python manage.py test AutoGrader.tests_no_wildcard_invalidation AutoGrader.tests_cache_invalidation_coverage AutoGrader.tests_migration_rollback_defaults AutoGrader.tests_redis_test_isolation AutoGrader.tests_beat_health classrooms.tests_teacher_access_sweep classrooms.tests_course_roster_scope_sweep assignments.tests_schema_extension users.tests_schema_extension --settings=settings_worktree --noinput > $OUT/guards_$TIP.log 2>&1
rc=$?; echo "exit=$rc wall_s=$(( $(date +%s)-s ))" >> $OUT/guards_$TIP.log
say "9 guards exit=$rc"
say "CHAIN END"
