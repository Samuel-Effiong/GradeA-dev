#!/usr/bin/env bash
# Design probe, not a mutant battery: would a NON-BLOCKING post-commit bump
# keep the commit-race fix correct? Runs the race tests against two variants
# of AutoGrader/cache_generation.py in a disposable worktree:
#   async_0ms  - post-commit bump handed to a background thread, no delay
#   async_50ms - the same with a 50ms delay, standing in for queue latency
# Restores are verified by sha256 against the commit's blob. Each variant
# builds and destroys its own test database (no --keepdb), so nothing is
# left behind.
set -u
COMMIT=${PROBE_COMMIT:?set PROBE_COMMIT}
ROOT=/home/bond-servant-in-training/Documents/Projects/Grade-Automator-Plus
WT=/home/bond-servant-in-training/Documents/Projects/Grade-Automator-Plus-async-probe
TARGET=AutoGrader/cache_generation.py
OUT=/home/bond-servant-in-training/Documents/Projects/Grade-Automator-Plus-cache-commit-race/docs/evidence/cache_commit_race/06_async_variant_probe.log
MUTATE=/home/bond-servant-in-training/Documents/Projects/Grade-Automator-Plus-cache-commit-race/docs/evidence/cache_commit_race/scripts/async_variant_apply.py

: > "$OUT"
log() { echo "$*" | tee -a "$OUT"; }
log "=== async post-commit bump probe on $(git -C "$ROOT" rev-parse "$COMMIT"), $(date -Iseconds)"
log "load_before=$(cut -d' ' -f1-3 /proc/loadavg)"
git -C "$ROOT" worktree add --detach "$WT" "$COMMIT" >> "$OUT" 2>&1
ln -s "$ROOT/.env" "$WT/.env"
{
    echo 'from AutoGrader.settings import *  # noqa: F401,F403'
    echo 'from AutoGrader.settings import DATABASES'
    echo ''
    echo 'DATABASES["default"].setdefault("TEST", {})'
    echo 'DATABASES["default"]["TEST"]["NAME"] = "test_async_probe"'
} > "$WT/settings_worktree.py"
BLOB_SHA=$(git -C "$ROOT" show "$COMMIT:$TARGET" | sha256sum | cut -d' ' -f1)
log "blob_sha256=$BLOB_SHA"
cd "$WT" || exit 1

run_variant() {
    local name=$1 delay=$2
    log ""
    log "--- variant $name"
    python "$MUTATE" "$TARGET" "$delay" 2>&1 | tee -a "$OUT"
    log "variant_sha256=$(sha256sum "$TARGET" | cut -d' ' -f1)"
    python manage.py test AutoGrader.tests_cache_commit_race --settings=settings_worktree --noinput -v2 > "/tmp/async_probe_$name.log" 2>&1
    log "test_exit=$?"
    grep -oE "^(FAIL|ERROR): test_[a-z_]+" "/tmp/async_probe_$name.log" | tee -a "$OUT"
    grep -E "^Ran |^OK|^FAILED" "/tmp/async_probe_$name.log" | tee -a "$OUT"
    git show "$COMMIT:$TARGET" > "$TARGET"
    local restored
    restored=$(sha256sum "$TARGET" | cut -d' ' -f1)
    if [ "$restored" = "$BLOB_SHA" ]; then log "restored_sha256 MATCHES blob"; else log "restored_sha256 MISMATCH"; fi
}

run_variant async_0ms 0
run_variant async_50ms 0.05

cd / || exit 1
git -C "$ROOT" worktree remove --force "$WT" && git -C "$ROOT" worktree prune
log "load_after=$(cut -d' ' -f1-3 /proc/loadavg)"
log "=== finished $(date -Iseconds); disposable worktree removed"
