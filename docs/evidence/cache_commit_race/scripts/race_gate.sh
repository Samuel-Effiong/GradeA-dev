#!/usr/bin/env bash
# H-1 Stage 3 strict final gate, per the owner's procedure (2026-09-14).
set -u

COMMIT=${GATE_COMMIT:?set GATE_COMMIT}
SHORT=${COMMIT:0:7}
ROOT=/home/bond-servant-in-training/Documents/Projects/Grade-Automator-Plus
WT=/home/bond-servant-in-training/Documents/Projects/Grade-Automator-Plus-race-gate-${GATE_RUN:?set GATE_RUN}
DB=test_race_gate_${SHORT}_${GATE_RUN}
OUT=/tmp/claude-1000/-home-bond-servant-in-training-Documents-Projects-Grade-Automator-Plus/df88a460-0c65-4ec7-9e64-0cfd512fe406/scratchpad/race_gate_${GATE_RUN}
REPORT=$OUT/gate_report.txt

mkdir -p "$OUT"
: > "$REPORT"
log() { echo "$*" | tee -a "$REPORT"; }

fingerprint() {
    echo "HEAD=$(git -C "$WT" rev-parse HEAD)"
    echo "index_sha256=$(git -C "$WT" ls-files -s | sha256sum | cut -d' ' -f1)"
    echo "content_sha256=$(cd "$WT" && git ls-files -z | xargs -0 sha256sum | sha256sum | cut -d' ' -f1)"
    echo "status_lines=$(git -C "$WT" status --porcelain | wc -l)"
}

# Postgres checks through Django's own connection to the main database, so
# no credentials are handled here.
pg_check() {
    (cd "$WT" && python manage.py shell --settings=settings_worktree -c "
from django.db import connection
with connection.cursor() as c:
    c.execute('select count(*) from pg_database where datname=%s', ['$DB'])
    print('pg_database_rows=%d' % c.fetchone()[0])
    c.execute('select count(*) from pg_stat_activity where datname=%s', ['$DB'])
    print('pg_stat_activity_connections=%d' % c.fetchone()[0])
" 2>/dev/null | grep -E '^pg_')
}

log "=== commit-race strict gate on $COMMIT (started $(date -Iseconds))"
log "disk_available_gb=$(df -BG --output=avail /home | tail -1 | tr -dc 0-9)"

git -C "$ROOT" worktree add --detach "$WT" "$COMMIT" 2>&1 | tee -a "$REPORT"
ln -s "$ROOT/.env" "$WT/.env"
cat > "$WT/settings_worktree.py" <<EOF
from AutoGrader.settings import *  # noqa: F401,F403
from AutoGrader.settings import DATABASES

DATABASES["default"].setdefault("TEST", {})
DATABASES["default"]["TEST"]["NAME"] = "$DB"
EOF

log "--- fingerprint BEFORE"
fingerprint | tee "$OUT/fingerprint_before.txt" | tee -a "$REPORT"

log "--- test database before (must be absent)"
pg_check | tee -a "$REPORT"

cd "$WT"

log "--- pre-commit run --all-files ($(date -Iseconds))"
pre-commit run --all-files > "$OUT/precommit_all_files.log" 2>&1
log "pre_commit_exit=$?"

log "--- check_migration_safety.py --base beta"
python scripts/check_migration_safety.py --base beta 2>&1 | tee -a "$REPORT"
log "migration_safety_exit=${PIPESTATUS[0]}"

log "--- manage.py check / makemigrations --check"
python manage.py check --settings=settings_worktree 2>&1 | tail -1 | tee -a "$REPORT"
log "check_exit=${PIPESTATUS[0]}"
python manage.py makemigrations --check --dry-run --settings=settings_worktree 2>&1 | tail -1 | tee -a "$REPORT"
log "makemigrations_check_exit=${PIPESTATUS[0]}"

log "--- full test suite, fresh DB $DB, --parallel 1, sleep inhibited ($(date -Iseconds))"
systemd-inhibit --what=sleep:idle python manage.py test --settings=settings_worktree --noinput --parallel 1 -v 1 > "$OUT/full_suite.log" 2>&1
TEST_EXIT=$?
log "test_exit=$TEST_EXIT finished $(date -Iseconds)"
log "full_output_lines=$(wc -l < "$OUT/full_suite.log") full_output_sha256=$(sha256sum "$OUT/full_suite.log" | cut -d' ' -f1)"
grep -E '^Ran [0-9]+ tests|^OK|^FAILED|^Found [0-9]+ test' "$OUT/full_suite.log" | tee -a "$REPORT"
log "fail_or_error_lines=$(grep -cE '^(FAIL|ERROR):' "$OUT/full_suite.log")"
log "other_sessions_teardown_lines=$(grep -ci 'other session' "$OUT/full_suite.log")"
grep -E 'Destroying test database' "$OUT/full_suite.log" | tee -a "$REPORT"

log "--- fingerprint AFTER"
fingerprint | tee "$OUT/fingerprint_after.txt" | tee -a "$REPORT"
if diff -q "$OUT/fingerprint_before.txt" "$OUT/fingerprint_after.txt" > /dev/null; then
    log "fingerprint_identical=yes"
else
    log "fingerprint_identical=NO"
fi

log "--- test database after (must be gone, no connections)"
pg_check | tee -a "$REPORT"

log "=== gate finished $(date -Iseconds)"

# The worktree is removed by the caller after the report is read.
