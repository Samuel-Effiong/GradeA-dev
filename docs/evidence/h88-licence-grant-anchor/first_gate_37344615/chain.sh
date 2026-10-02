#!/bin/bash
# H-88 / H-93 / H-81 gate chain on the frozen tip. Stops on the first
# unexpected result. Usage: chain.sh <tip-sha8> <base-sha8>
set -u
TIP=$1; BASE=$2
P=/home/bond-servant-in-training/Documents/Projects
WT=$P/Grade-Automator-Plus-h88-licence-grant-anchor
RP=$P/Grade-Automator-Plus-h66-repro
OUT=$P/GAP-d5-runs/h88
export EXEMPT_EMAIL_DOMAINS=
WRAP="systemd-inhibit --what=idle:sleep --who=GAP --why=GAP-test-run --mode=block systemd-run --user --scope -q -p MemoryMax=6G -p MemorySwapMax=0 nice -n 10 timeout -k 60 1800"
FIXED="billing/license_service.py billing/services.py billing/refresh_timing.py billing/models.py"
MIG=billing/migrations/0073_schoolcreditallocation_grant_anchor_at.py
stamp() { while IFS= read -r l; do printf '%s %s\n' "$(date +%H:%M:%S)" "$l"; done; }
say() { echo "$(date +%H:%M:%S) $*" | tee -a $OUT/chain.status; }

cd $WT || exit 9
[ "$(git rev-parse --short=8 HEAD)" = "$TIP" ] && [ -z "$(git status --porcelain)" ] || { say "ABORT: tree not frozen at $TIP"; exit 9; }

# repro: the tip's e2e tests over the base's code, without the migration
cd $RP || exit 9
git checkout -q --detach $TIP && git checkout -q $BASE -- $FIXED && rm -f $MIG || { say "ABORT: repro checkout"; exit 9; }
s=$(date +%s)
$WRAP python manage.py test billing.tests.test_licence_grant_anchor billing.tests.test_owed_grant_detection --settings=settings_worktree --noinput > $OUT/repro_${TIP}_tests_over_${BASE}_code.log 2>&1
rc=$?; echo "exit=$rc wall_s=$(( $(date +%s)-s ))" >> $OUT/repro_${TIP}_tests_over_${BASE}_code.log
git checkout -q $TIP -- $FIXED $MIG
[ -z "$(git status --porcelain --untracked-files=no)" ] || { say "ABORT: repro worktree not restored"; exit 9; }
say "repro exit=$rc (red expected)"
[ $rc -eq 1 ] || { say "STOP: repro was not a plain test failure"; exit 1; }

cd $WT
s=$(date +%s)
$WRAP python manage.py test billing.tests.test_licence_grant_anchor billing.tests.test_allocation_anchor billing.tests.test_owed_grant_detection billing.tests.test_license_multi_month_budget billing.tests.test_monthly_rollover_cleanup_race billing.tests.test_beat_lock_catch_up billing.tests.test_license_renewal_partial_failure billing.tests.test_annual_grant_anchor billing.tests.test_next_monthly_grant billing.tests.test_annual_mid_cycle_grants billing.tests.test_license_service billing.tests.test_logs_carry_no_email --settings=settings_worktree --noinput > $OUT/a_modules_$TIP.log 2>&1
rc=$?; echo "exit=$rc wall_s=$(( $(date +%s)-s ))" >> $OUT/a_modules_$TIP.log
say "(a) exit=$rc"; [ $rc -eq 0 ] || { say "STOP: (a) red"; exit 1; }

# (b) in two parts, each inside its own wrapper (one cap and one timeout each)
for part in "M:M1,M2,M3,M4,M5,M6,M7,M8,M9,M10,M11,M12,M13,M14" "WO:W1,W2,W3,O1,O2,O3,O4,O5,O6,O7,O8"; do
  name=${part%%:*}; only=${part#*:}
  s=$(date +%s)
  PYTHONDONTWRITEBYTECODE=1 $WRAP python docs/evidence/h88-licence-grant-anchor/run_mutants.py $TIP --only $only > $OUT/b_mutation_battery_${name}_$TIP.log 2>&1
  rc=$?; echo "exit=$rc wall_s=$(( $(date +%s)-s ))" >> $OUT/b_mutation_battery_${name}_$TIP.log
  [ -f docs/evidence/h88-licence-grant-anchor/logs/baseline.log ] && mv docs/evidence/h88-licence-grant-anchor/logs/baseline.log docs/evidence/h88-licence-grant-anchor/logs/baseline_$name.log
  say "(b) part $name exit=$rc"
  [ -z "$(git status --porcelain --untracked-files=no)" ] || { say "STOP: tracked files changed after (b)"; exit 1; }
  [ $rc -eq 0 ] || { say "STOP: (b) part $name not all killed"; exit 1; }
done

s=$(date +%s)
$WRAP python manage.py test billing users classrooms dashboard AutoGrader.tests_no_wildcard_invalidation AutoGrader.tests_cache_invalidation_coverage AutoGrader.tests_migration_rollback_defaults AutoGrader.tests_redis_test_isolation AutoGrader.tests_beat_health classrooms.tests_teacher_access_sweep classrooms.tests_course_roster_scope_sweep assignments.tests_schema_extension users.tests_schema_extension --settings=settings_worktree --noinput --parallel 2 --verbosity 2 2>&1 | stamp > $OUT/c_apps_guards_$TIP.log
rc=${PIPESTATUS[0]}; echo "exit=$rc wall_s=$(( $(date +%s)-s ))" >> $OUT/c_apps_guards_$TIP.log
say "(c) exit=$rc"
say "CHAIN END"
