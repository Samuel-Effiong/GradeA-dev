#!/usr/bin/env bash
# Strict final gate for H-18/H-19 on a committed tree.
set -u
COMMIT=681152744a65a93deac2ee830fc61ab543cc75f8
RUN=$2
G=/home/bond-servant-in-training/Documents/Projects/Grade-Automator-Plus-h18h19-gate-6811527-$RUN
MAIN=/home/bond-servant-in-training/Documents/Projects/Grade-Automator-Plus
OUT=$1
DB=test_h18h19_gate_6811527_$RUN
mkdir -p "$OUT"
log(){ echo "[$(date -Is)] $*" | tee -a "$OUT/00_steps.txt"; }

fingerprint(){ (cd "$G" && {
  echo "HEAD $(git rev-parse HEAD)"
  echo "index_sha256 $(git ls-files -s | sha256sum | cut -d' ' -f1)"
  echo "tracked_content_sha256 $(git ls-files -z | xargs -0 sha256sum | sha256sum | cut -d' ' -f1)"
  echo "porcelain_lines $(git status --porcelain | wc -l)"
  echo "porcelain_lines_excluding_team_symlink $(git status --porcelain | grep -Fvx '?? team' | wc -l)"
}); }
pgcheck(){ (cd "$G" && python manage.py shell --settings=settings_worktree -c "
from django.db import connection
c=connection.cursor()
c.execute('select count(*) from pg_database where datname=%s',['$DB']); print('pg_database rows for $DB:', c.fetchone()[0])
c.execute('select count(*) from pg_stat_activity where datname=%s',['$DB']); print('pg_stat_activity connections to $DB:', c.fetchone()[0])
" 2>&1 | grep -E "^pg_"); }

log "gate start commit=$COMMIT"
cd "$MAIN" && git worktree add --detach "$G" "$COMMIT" > "$OUT/01_worktree_add.txt" 2>&1
git -C "$MAIN" worktree lock --reason "H-18/H-19 strict final gate" "$G"
ln -s "$MAIN/.env" "$G/.env"
cat > "$G/settings_worktree.py" <<PY
"""H-18/H-19 strict final gate: unique test database."""
from AutoGrader.settings import *  # noqa: F401,F403
from AutoGrader.settings import DATABASES

DATABASES["default"].setdefault("TEST", {})
DATABASES["default"]["TEST"]["NAME"] = "$DB"
PY
cd "$G"
fingerprint > "$OUT/02_fingerprint_before.txt"; cat "$OUT/02_fingerprint_before.txt" | tee -a "$OUT/00_steps.txt"
pgcheck > "$OUT/03_pg_before.txt"; cat "$OUT/03_pg_before.txt" | tee -a "$OUT/00_steps.txt"
python manage.py shell --settings=settings_worktree -c "
from django.db import connection; import django, sys, redis
from django.conf import settings
c=connection.cursor(); c.execute('select version()'); print('postgres:', c.fetchone()[0])
from django_redis import get_redis_connection
print('redis:', get_redis_connection('default').info()['redis_version'])
print('python:', sys.version.split()[0], ' django:', django.get_version())
" > "$OUT/04_infra.txt" 2>&1; cat "$OUT/04_infra.txt" | tee -a "$OUT/00_steps.txt"

log "pre-commit --all-files"
pre-commit run --all-files > "$OUT/10_precommit_all_files.log" 2>&1; log "precommit_exit=$?"
log "check_migration_safety --base beta"
python scripts/check_migration_safety.py --base beta > "$OUT/11_migration_safety.log" 2>&1; log "migration_safety_exit=$?"
log "makemigrations --check --dry-run"
python manage.py makemigrations --check --dry-run --settings=settings_worktree > "$OUT/12_makemigrations_check.log" 2>&1; log "makemigrations_exit=$?"
log "manage.py check"
python manage.py check --settings=settings_worktree > "$OUT/13_check.log" 2>&1; log "check_exit=$?"
fingerprint > "$OUT/14_fingerprint_after_static.txt"

log "full suite start (fresh DB, no --keepdb)"
python manage.py test --noinput --settings=settings_worktree > "$OUT/20_full_suite.log" 2>&1; log "full_suite_exit=$?"
log "$(grep -E '^Ran [0-9]+ tests' "$OUT/20_full_suite.log")"
log "$(grep -E '^(OK|FAILED)' "$OUT/20_full_suite.log" | tail -1)"
log "other-sessions lines: $(grep -ci 'other session' "$OUT/20_full_suite.log")"
log "destroying lines: $(grep -c 'Destroying test database' "$OUT/20_full_suite.log")"

fingerprint > "$OUT/30_fingerprint_after.txt"; cat "$OUT/30_fingerprint_after.txt" | tee -a "$OUT/00_steps.txt"
if diff -q "$OUT/02_fingerprint_before.txt" "$OUT/30_fingerprint_after.txt" >/dev/null && diff -q "$OUT/02_fingerprint_before.txt" "$OUT/14_fingerprint_after_static.txt" >/dev/null; then log "fingerprint UNCHANGED"; else log "fingerprint CHANGED"; fi
pgcheck > "$OUT/31_pg_after.txt"; cat "$OUT/31_pg_after.txt" | tee -a "$OUT/00_steps.txt"
for f in "$OUT"/*.log; do echo "$(wc -l < "$f") $(sha256sum "$f")"; done > "$OUT/RAW_LOG_LINES_SHA256.txt"
log "gate end"
