#!/usr/bin/env bash
# Mutation battery for the cache commit-race fix, in a disposable worktree.
set -u
COMMIT=a183fb3
ROOT=/home/bond-servant-in-training/Documents/Projects/Grade-Automator-Plus
WT=/home/bond-servant-in-training/Documents/Projects/Grade-Automator-Plus-mut-race
TARGET=AutoGrader/cache_generation.py
OUT=/home/bond-servant-in-training/Documents/Projects/Grade-Automator-Plus-cache-commit-race/docs/evidence/cache_commit_race/04_mutation_battery.log

: > "$OUT"
log() { echo "$*" | tee -a "$OUT"; }

log "=== mutation battery on $(git -C "$ROOT" rev-parse $COMMIT) started $(date -Iseconds)"
git -C "$ROOT" worktree add --detach "$WT" "$COMMIT" >> "$OUT" 2>&1
ln -s "$ROOT/.env" "$WT/.env"
cat > "$WT/settings_worktree.py" <<EOF
from AutoGrader.settings import *  # noqa: F401,F403
from AutoGrader.settings import DATABASES

DATABASES["default"].setdefault("TEST", {})
DATABASES["default"]["TEST"]["NAME"] = "test_mut_race"
EOF
BLOB_SHA=$(git -C "$ROOT" show "$COMMIT:$TARGET" | sha256sum | cut -d' ' -f1)
log "blob_sha256($TARGET@$COMMIT)=$BLOB_SHA"

cd "$WT"
first=1
run_mutant() {
    local name=$1 old=$2 new=$3
    log ""
    log "--- mutant $name"
    python - "$TARGET" "$old" "$new" <<'PY' 2>&1 | tee -a "$OUT"
import sys
path, old, new = sys.argv[1], sys.argv[2], sys.argv[3]
s = open(path).read()
assert s.count(old) == 1, f"pattern found {s.count(old)} times"
open(path, "w").write(s.replace(old, new))
print("applied")
PY
    log "mutated_sha256=$(sha256sum "$TARGET" | cut -d' ' -f1)"
    local keep="--keepdb"
    if [ "$first" = 1 ]; then keep=""; first=0; fi
    python manage.py test AutoGrader.tests_cache_commit_race --settings=settings_worktree --noinput $keep -v2 > "/tmp/mut_race_$name.log" 2>&1
    log "test_exit=$?"
    grep -oE "^(FAIL|ERROR): test_[a-z_]+" "/tmp/mut_race_$name.log" | tee -a "$OUT"
    grep -E "^Ran |^OK|^FAILED" "/tmp/mut_race_$name.log" | tee -a "$OUT"
    git show "$COMMIT:$TARGET" > "$TARGET"
    local restored
    restored=$(sha256sum "$TARGET" | cut -d' ' -f1)
    if [ "$restored" = "$BLOB_SHA" ]; then log "restored_sha256=$restored MATCHES blob"; else log "restored_sha256=$restored MISMATCH"; fi
}

run_mutant a_drop_post_commit_bump \
'    if transaction.get_connection().in_atomic_block:
        transaction.on_commit(lambda: _bump_now(unique))' \
'    if False:  # MUTANT a
        transaction.on_commit(lambda: _bump_now(unique))'

run_mutant b_post_commit_bump_everywhere \
'    if transaction.get_connection().in_atomic_block:
        transaction.on_commit(lambda: _bump_now(unique))' \
'    if True:  # MUTANT b
        transaction.on_commit(lambda: _bump_now(unique))'

run_mutant c_skip_immediate_bump_in_transaction \
'    bumped = _bump_now(unique)' \
'    bumped = 0 if transaction.get_connection().in_atomic_block else _bump_now(unique)  # MUTANT c'

log ""
log "--- control: unmutated tree"
python manage.py test AutoGrader.tests_cache_commit_race --settings=settings_worktree --noinput -v2 > /tmp/mut_race_control.log 2>&1
log "test_exit=$?"
grep -E "^Ran |^OK|^FAILED" /tmp/mut_race_control.log | tee -a "$OUT"
log "final_sha256=$(sha256sum "$TARGET" | cut -d' ' -f1) status_lines=$(git status --porcelain --untracked-files=no | wc -l)"

cd /
git -C "$ROOT" worktree remove --force "$WT" && git -C "$ROOT" worktree prune
log "=== finished $(date -Iseconds); disposable worktree removed"
