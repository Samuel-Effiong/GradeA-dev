#!/bin/bash
# H-85 gate (c) only, after the pause: billing + the 9 guards on the frozen tip.
set -u
TIP=273c2b30
P=/home/bond-servant-in-training/Documents/Projects
WT=$P/Grade-Automator-Plus-h85-neutral-subscription-refusal
OUT=$P/GAP-d5-runs/h85
LOCK=/home/bond-servant-in-training/.machine-fullsuite.lock
export EXEMPT_EMAIL_DOMAINS=
PRE="systemd-inhibit --what=idle:sleep:handle-lid-switch --who=GAP --why=GAP-test-run --mode=block systemd-run --user --scope -q -p MemoryMax=6G -p MemorySwapMax=0 nice -n 10"
stamp() { while IFS= read -r l; do printf '%s %s\n' "$(date +%H:%M:%S)" "$l"; done; }
say() { echo "$(date +%H:%M:%S) $*" | tee -a $OUT/chain.status; }
cd $WT || exit 9
[ "$(git rev-parse --short=8 HEAD)" = "$TIP" ] && [ -z "$(git status --porcelain --untracked-files=no)" ] || { say "ABORT: tree not frozen at $TIP"; exit 9; }
s=$(date +%s)
$PRE flock $LOCK timeout -k 60 1800 python manage.py test billing AutoGrader.tests_no_wildcard_invalidation AutoGrader.tests_cache_invalidation_coverage AutoGrader.tests_migration_rollback_defaults AutoGrader.tests_redis_test_isolation AutoGrader.tests_beat_health classrooms.tests_teacher_access_sweep classrooms.tests_course_roster_scope_sweep assignments.tests_schema_extension users.tests_schema_extension --settings=settings_worktree --noinput --parallel 2 --verbosity 2 2>&1 | stamp > $OUT/c_app_billing_guards_$TIP.log
rc=${PIPESTATUS[0]}; echo "exit=$rc wall_s=$(( $(date +%s)-s ))" >> $OUT/c_app_billing_guards_$TIP.log
say "(c) exit=$rc (resumed under a new GRANT)"
say "CHAIN END"
