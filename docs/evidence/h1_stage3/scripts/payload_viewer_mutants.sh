#!/usr/bin/env bash
# Mutation battery for the H-1 Stage 3 payload-viewer fix, in a disposable
# worktree detached at the commit. Each mutant is applied, its tests run,
# and the file is restored from the commit's blob and checked by sha256.
set -u
COMMIT=${MUT_COMMIT:?set MUT_COMMIT}
ROOT=/home/bond-servant-in-training/Documents/Projects/Grade-Automator-Plus
S3=/home/bond-servant-in-training/Documents/Projects/Grade-Automator-Plus-h1-stage3-wildcard-removal
WT=/home/bond-servant-in-training/Documents/Projects/Grade-Automator-Plus-pv-mut
DEF=$S3/docs/evidence/h1_stage3/scripts/payload_viewer_mutants.py
OUT=$S3/docs/evidence/h1_stage3/payload_viewer_mutation_battery.log

: > "$OUT"
log() { echo "$*" | tee -a "$OUT"; }
log "=== payload-viewer mutation battery on $(git -C "$ROOT" rev-parse "$COMMIT"), $(date -Iseconds), load=$(cut -d' ' -f1-3 /proc/loadavg)"
git -C "$ROOT" worktree add --detach "$WT" "$COMMIT" >> "$OUT" 2>&1
ln -s "$ROOT/.env" "$WT/.env"
{
    echo 'from AutoGrader.settings import *  # noqa: F401,F403'
    echo 'from AutoGrader.settings import DATABASES'
    echo ''
    echo 'DATABASES["default"].setdefault("TEST", {})'
    echo 'DATABASES["default"]["TEST"]["NAME"] = "test_pv_mut"'
} > "$WT/settings_worktree.py"
cd "$WT" || exit 1

first=1
for name in $(python "$DEF" names x); do
    path=$(python "$DEF" path "$name")
    tests=$(python "$DEF" tests "$name")
    blob=$(git show "$COMMIT:$path" | sha256sum | cut -d' ' -f1)
    log ""
    log "--- mutant $name ($path) expected catchers: $tests"
    python "$DEF" apply "$name" 2>&1 | tee -a "$OUT"
    log "mutated_sha256=$(sha256sum "$path" | cut -d' ' -f1) blob_sha256=$blob"
    keep="--keepdb"
    if [ "$first" = 1 ]; then keep=""; first=0; fi
    log "load_before_mutant=$(cut -d' ' -f1-3 /proc/loadavg)"
    # shellcheck disable=SC2086
    nice -n 19 python manage.py test $tests --settings=settings_worktree --noinput $keep -v2 > "/tmp/pv_mut_$name.log" 2>&1
    log "test_exit=$?"
    grep -oE "^(FAIL|ERROR): test_[a-z0-9_]+" "/tmp/pv_mut_$name.log" | sort | uniq -c | tee -a "$OUT"
    grep -E "^Ran |^OK|^FAILED" "/tmp/pv_mut_$name.log" | tee -a "$OUT"
    # A kill only counts if an assertion about a specific view or generation
    # caught it - not a timeout or an unrelated error under low CPU priority.
    log "kill_reasons: stale_or_spurious=$(grep -c 'STALE/SPURIOUS' "/tmp/pv_mut_$name.log") did_not_move=$(grep -c 'did not move' "/tmp/pv_mut_$name.log") over_invalidation=$(grep -c 'over-invalidation' "/tmp/pv_mut_$name.log") query_count=$(grep -c 'costs\|cost exactly\|queries grew' "/tmp/pv_mut_$name.log") timeouts=$(grep -ciE 'timed? ?out|TimeoutError' "/tmp/pv_mut_$name.log") errors=$(grep -c '^ERROR: ' "/tmp/pv_mut_$name.log")"
    git show "$COMMIT:$path" > "$path"
    restored=$(sha256sum "$path" | cut -d' ' -f1)
    if [ "$restored" = "$blob" ]; then log "restored MATCHES blob"; else log "restored MISMATCH"; fi
done

log ""
log "--- control: unmutated tree, no --keepdb"
nice -n 19 python manage.py test users.tests_cache_matrix_payload_viewers AutoGrader.tests_cache_user_fanout --settings=settings_worktree --noinput -v2 > /tmp/pv_mut_control.log 2>&1
log "test_exit=$?"
grep -E "^Ran |^OK|^FAILED" /tmp/pv_mut_control.log | tee -a "$OUT"
log "status_lines=$(git status --porcelain --untracked-files=no | wc -l)"
cd / || exit 1
git -C "$ROOT" worktree remove --force "$WT" && git -C "$ROOT" worktree prune
log "load_after=$(cut -d' ' -f1-3 /proc/loadavg)"
log "=== finished $(date -Iseconds); disposable worktree removed"
