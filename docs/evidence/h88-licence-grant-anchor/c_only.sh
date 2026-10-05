#!/bin/bash
# H-88 gate (c) only, resumed after a pause at 0b's request.
TIP=8ea91e21
# H-88 / H-93 / H-81 gate chain on the frozen tip. Stops on the first
# unexpected result. Usage: chain.sh <tip-sha8> <base-sha8>
set -u
P=/home/bond-servant-in-training/Documents/Projects
WT=$P/Grade-Automator-Plus-h88-licence-grant-anchor
RP=$P/Grade-Automator-Plus-h66-repro
OUT=$P/GAP-d5-runs/h88
export EXEMPT_EMAIL_DOMAINS=
PRE="systemd-inhibit --what=idle:sleep:handle-lid-switch --who=GAP --why=GAP-test-run --mode=block systemd-run --user --scope -q -p MemoryMax=6G -p MemorySwapMax=0 nice -n 10"
WRAP="$PRE timeout -k 60 1800"
LOCK=/home/bond-servant-in-training/.machine-fullsuite.lock
FIXED="billing/license_service.py billing/services.py billing/refresh_timing.py billing/models.py billing/qa_time_travel.py"
MIG=billing/migrations/0073_schoolcreditallocation_grant_anchor_at.py
stamp() { while IFS= read -r l; do printf '%s %s\n' "$(date +%H:%M:%S)" "$l"; done; }
say() { echo "$(date +%H:%M:%S) $*" | tee -a $OUT/chain.status; }
# A step boundary: stop cleanly if $OUT/PAUSE exists (0b asked for the slot).
boundary() { [ ! -e $OUT/PAUSE ] || { say "PAUSED before $1 (PAUSE file present)"; exit 3; }; }

cd $WT || exit 9
[ "$(git rev-parse --short=8 HEAD)" = "$TIP" ] && [ -z "$(git status --porcelain --untracked-files=no)" ] || { say "ABORT: tree not frozen at $TIP"; exit 9; }
s=$(date +%s)
$PRE flock $LOCK timeout -k 60 1800 python manage.py test billing users classrooms dashboard AutoGrader.tests_no_wildcard_invalidation AutoGrader.tests_cache_invalidation_coverage AutoGrader.tests_migration_rollback_defaults AutoGrader.tests_redis_test_isolation AutoGrader.tests_beat_health classrooms.tests_teacher_access_sweep classrooms.tests_course_roster_scope_sweep assignments.tests_schema_extension users.tests_schema_extension --settings=settings_worktree --noinput --parallel 2 --verbosity 2 2>&1 | stamp > $OUT/c_apps_guards_$TIP.log
rc=${PIPESTATUS[0]}; echo "exit=$rc wall_s=$(( $(date +%s)-s ))" >> $OUT/c_apps_guards_$TIP.log
say "(c) exit=$rc"
say "CHAIN END"
