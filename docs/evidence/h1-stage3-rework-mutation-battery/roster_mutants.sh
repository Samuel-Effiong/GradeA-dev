#!/usr/bin/env bash
# Mutation battery for the stage 3 course-roster-scope rework, in a disposable
# detached worktree with its own database. Usage: roster_mutants.sh <commit>
set -u
COMMIT=$1
ROOT=/home/bond-servant-in-training/Documents/Projects/Grade-Automator-Plus
WT=/home/bond-servant-in-training/Documents/Projects/Grade-Automator-Plus-mut-roster
S=/tmp/claude-1000/-home-bond-servant-in-training-Documents-Projects-Grade-Automator-Plus/445eb991-9360-4dd7-b8be-0d9a53590846/scratchpad
OUT=$S/roster_mutation_battery.log
TESTS="classrooms.tests_cache_course_roster_scope classrooms.tests_course_roster_scope_sweep"
export RACE_COST_ENROLLMENTS=600 RACE_COST_ROWS=200 EXEMPT_EMAIL_DOMAINS=

: > "$OUT"
log() { echo "$*" | tee -a "$OUT"; }
log "=== roster-scope mutation battery on $(git -C "$ROOT" rev-parse "$COMMIT") started $(date -Iseconds)"
git -C "$ROOT" worktree add --detach "$WT" "$COMMIT" >> "$OUT" 2>&1
ln -s "$ROOT/.env" "$WT/.env"
printf '%s\n' 'from AutoGrader.settings import *  # noqa: F401,F403' 'from AutoGrader.settings import DATABASES' '' 'DATABASES["default"].setdefault("TEST", {})' 'DATABASES["default"]["TEST"]["NAME"] = "test_mut_roster"' > "$WT/settings_worktree.py"
cd "$WT"

run_tests() {  # $1 = log name
    systemd-run --user --scope -q -p MemoryMax=6G -p MemorySwapMax=0 nice -n 10 timeout -k 60 900 python manage.py test $TESTS --settings=settings_worktree --noinput --keepdb -v 2 > "$S/mut_roster_$1.log" 2>&1
    local rc=$?
    log "test_exit=$rc"
    { [ $rc = 124 ] || [ $rc = 137 ]; } && log "TIMEOUT (hang)"
    grep -oE "^(FAIL|ERROR): test_[a-z0-9_]+" "$S/mut_roster_$1.log" | sort | uniq -c | tee -a "$OUT"
    grep -E "^Ran |^OK|^FAILED" "$S/mut_roster_$1.log" | tee -a "$OUT"
}

run_mutant() {  # name file old new
    local name=$1 file=$2 old=$3 new=$4
    local blob
    blob=$(git show "$COMMIT:$file" | sha256sum | cut -d' ' -f1)
    log ""
    log "--- mutant $name ($file)"
    python - "$file" "$old" "$new" <<'PY' 2>&1 | tee -a "$OUT"
import sys
path, old, new = sys.argv[1], sys.argv[2], sys.argv[3]
s = open(path).read()
assert s.count(old) == 1, f"pattern found {s.count(old)} times"
open(path, "w").write(s.replace(old, new))
print("applied")
PY
    run_tests "$name"
    git show "$COMMIT:$file" > "$file"
    local restored
    restored=$(sha256sum "$file" | cut -d' ' -f1)
    if [ "$restored" = "$blob" ]; then log "restored_sha256=$restored MATCHES blob"; else log "restored_sha256=$restored MISMATCH"; fi
}

log ""
log "--- control: unmutated tree (creates the test DB)"
systemd-run --user --scope -q -p MemoryMax=6G -p MemorySwapMax=0 nice -n 10 timeout -k 60 900 python manage.py test $TESTS --settings=settings_worktree --noinput --keepdb -v 2 > "$S/mut_roster_control.log" 2>&1
log "test_exit=$?"
grep -E "^Ran |^OK|^FAILED" "$S/mut_roster_control.log" | tee -a "$OUT"

run_mutant M1_classmate_fanout_restored classrooms/signals.py \
'        + _course_owner_scopes(getattr(instance, "course", None))' \
'        + _course_scopes(getattr(instance, "course", None))  # MUTANT M1'

run_mutant M2_no_crs_on_student_detail classrooms/views.py \
'            return [(SCOPE_COURSE, course_id)]' \
'            return []  # MUTANT M2'

run_mutant M3_no_crs_on_student_list classrooms/views.py \
'        return [(SCOPE_COURSE, course_id) for course_id in course_ids]' \
'        return []  # MUTANT M3'

run_mutant M4_my_courses_without_global classrooms/views.py \
'            [(SCOPE_USER, request.user.id), (SCOPE_GLOBAL, None)],' \
'            [(SCOPE_USER, request.user.id)],  # MUTANT M4'

run_mutant M5_override_removed classrooms/views.py \
'    def extra_cache_scopes(self, action):' \
'    def _unused_extra_cache_scopes(self, action):  # MUTANT M5'

run_mutant M6_batched_read_ignores_counters AutoGrader/cache_generation.py \
'        values = cache.get_many(keys)' \
'        values = {}  # MUTANT M6'

log ""
log "final_status_lines=$(git status --porcelain --untracked-files=no | wc -l)"
cd "$ROOT"
systemd-run --user --scope -q -p MemoryMax=2G nice -n 10 timeout 120 python "$ROOT/manage.py" shell -c "from django.db import connection; c=connection.cursor(); c.execute('DROP DATABASE IF EXISTS test_mut_roster'); print('dropped test_mut_roster')" >> "$OUT" 2>&1
git -C "$ROOT" worktree remove --force "$WT" && git -C "$ROOT" worktree prune
log "=== finished $(date -Iseconds); disposable worktree removed"
