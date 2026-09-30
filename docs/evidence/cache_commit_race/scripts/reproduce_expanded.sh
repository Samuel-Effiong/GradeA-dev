#!/usr/bin/env bash
# Reproduce the commit race on the unfixed base commit, using the FINAL test
# file. Production code is untouched: only the test files are copied in.
set -u
ROOT=/home/bond-servant-in-training/Documents/Projects/Grade-Automator-Plus
WT=/home/bond-servant-in-training/Documents/Projects/Grade-Automator-Plus-repro-race
FIX=/home/bond-servant-in-training/Documents/Projects/Grade-Automator-Plus-cache-commit-race
BASE=b744c9f
OUT=$FIX/docs/evidence/cache_commit_race/01_reproduce_on_unfixed_beta.log

: > "$OUT"
log() { echo "$*" | tee -a "$OUT"; }
log "=== reproduce on UNFIXED $BASE with the final test file, $(date -Iseconds)"
git -C "$ROOT" worktree add --detach "$WT" "$BASE" >> "$OUT" 2>&1
ln -s "$ROOT/.env" "$WT/.env"
cat > "$WT/settings_worktree.py" <<PY
from AutoGrader.settings import *  # noqa: F401,F403
from AutoGrader.settings import DATABASES

DATABASES["default"].setdefault("TEST", {})
DATABASES["default"]["TEST"]["NAME"] = "test_repro_race"
PY
cp "$FIX/AutoGrader/tests_cache_commit_race.py" "$WT/AutoGrader/"
log "production cache_generation.py on this tree: $(sha256sum "$WT/AutoGrader/cache_generation.py" | cut -d' ' -f1)"
log "same file at the base commit:                $(git -C "$ROOT" show "$BASE:AutoGrader/cache_generation.py" | sha256sum | cut -d' ' -f1)"
log "git status (test file only expected):"
git -C "$WT" status --porcelain | tee -a "$OUT"

cd "$WT"
python manage.py test AutoGrader.tests_cache_commit_race --settings=settings_worktree --noinput -v2 >> "$OUT" 2>&1
log "test_exit=$?"
grep -E "^(FAIL|ERROR): test_[a-z_]+" "$OUT" | tee -a "${OUT}.summary"
grep -E "^Ran |^OK|^FAILED" "$OUT" | tee -a "${OUT}.summary"
cd /
git -C "$ROOT" worktree remove --force "$WT" && git -C "$ROOT" worktree prune
log "=== finished $(date -Iseconds); worktree removed"
