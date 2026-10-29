#!/usr/bin/env bash
# S6a mutation battery in a disposable detached worktree with its own test DB.
# Usage: s6a_battery.sh <commit>
set -u
COMMIT=$1
ROOT=/home/bond-servant-in-training/Documents/Projects/Grade-Automator-Plus
WT=/home/bond-servant-in-training/Documents/Projects/Grade-Automator-Plus-mut-s6a
S=/tmp/claude-1000/-home-bond-servant-in-training-Documents-Projects-Grade-Automator-Plus/445eb991-9360-4dd7-b8be-0d9a53590846/scratchpad
OUT=$S/s6a_battery.log
TESTS="AutoGrader.tests_reason_codes audit.tests_emitter"
export EXEMPT_EMAIL_DOMAINS=

: > "$OUT"
log() { echo "$*" | tee -a "$OUT"; }
log "=== S6a mutation battery on $(git -C "$ROOT" rev-parse "$COMMIT") started $(date -Iseconds)"
git -C "$ROOT" worktree add --detach "$WT" "$COMMIT" >> "$OUT" 2>&1
ln -s "$ROOT/.env" "$WT/.env"
printf '%s\n' 'from AutoGrader.settings import *  # noqa: F401,F403' 'from AutoGrader.settings import DATABASES' '' 'DATABASES["default"].setdefault("TEST", {})' 'DATABASES["default"]["TEST"]["NAME"] = "test_mut_s6a"' > "$WT/settings_worktree.py"
cd "$WT"

run_tests() {  # $1 = log name
    systemd-run --user --scope -q -p MemoryMax=6G -p MemorySwapMax=0 nice -n 10 timeout -k 60 900 \
        python manage.py test $TESTS --settings=settings_worktree --noinput --keepdb -v 2 > "$S/mut_s6a_$1.log" 2>&1
    local rc=$?
    log "test_exit=$rc"
    grep -oE "^(FAIL|ERROR): test_[a-z0-9_]+" "$S/mut_s6a_$1.log" | sort | uniq -c | tee -a "$OUT"
    grep -E "^Ran |^OK|^FAILED" "$S/mut_s6a_$1.log" | tee -a "$OUT"
}

log ""
log "--- control: unmutated tree (creates the test DB)"
run_tests control

python3 - "$S" <<'PY' > "$S/s6a_mutant_names.txt"
import sys
sys.path.insert(0, sys.argv[1])
from s6a_mutants import MUTANTS
for name, *_ in MUTANTS:
    print(name)
PY

for name in $(cat "$S/s6a_mutant_names.txt"); do
    file=$(python3 - "$S" "$name" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
from s6a_mutants import MUTANTS
print(next(f for n, f, *_ in MUTANTS if n == sys.argv[2]))
PY
)
    blob=$(git show "$COMMIT:$file" | sha256sum | cut -d' ' -f1)
    log ""
    log "--- mutant $name ($file)"
    python3 - "$S" "$name" <<'PY' 2>&1 | tee -a "$OUT"
import sys
sys.path.insert(0, sys.argv[1])
from s6a_mutants import MUTANTS
name, path, old, new = next(m for m in MUTANTS if m[0] == sys.argv[2])
s = open(path).read()
assert s.count(old) == 1, f"pattern found {s.count(old)} times"
open(path, "w").write(s.replace(old, new))
print("applied")
PY
    run_tests "$name"
    git show "$COMMIT:$file" > "$file"
    restored=$(sha256sum "$file" | cut -d' ' -f1)
    if [ "$restored" = "$blob" ]; then log "restored_sha256=$restored MATCHES blob"; else log "restored_sha256=$restored MISMATCH"; fi
done

log ""
log "final_status_lines=$(git status --porcelain --untracked-files=no | wc -l)"
cd "$ROOT"
systemd-run --user --scope -q -p MemoryMax=2G nice -n 10 timeout 120 python manage.py shell -c "from django.db import connection; c=connection.cursor(); c.execute('DROP DATABASE IF EXISTS test_mut_s6a'); print('dropped test_mut_s6a')" 2>&1 | grep -v "objects imported" >> "$OUT"
git -C "$ROOT" worktree remove --force "$WT" && git -C "$ROOT" worktree prune
log "=== finished $(date -Iseconds); disposable worktree removed"
