#!/bin/bash
# v2's one slot for H-152 and its delta (the old activation door is closed;
# the conversion needs a queued email and goes on past one account):
# baseline (ed's four modules and
# v2's probe), then mutants U1 to U6, each judged by v2's probe only,
# each judgement to its own file (seven inner runs). Rules 12, 13, 16 wrap
# each step; rule 17 is in the runner; rule 18: every run writes straight
# to a file, stdin from /dev/null. Database tests, cache in memory
# (--keepdb, test_vf2_s1). Nothing is timed. No email leaves: the email
# task is replaced. The probe is copied in and removed at the end.
# Usage: vf_h152_run.sh <tip-sha8>      (only after the Release Engineer's GRANT)
set -uo pipefail
TIP=$1
P=/home/bond-servant-in-training/Documents/Projects
WT=$P/Grade-Automator-Plus-vf2-s1
H=$P/GAP-v2-handover
PROBE=classrooms/tests_vf2_h152_probe.py
PY=/home/bond-servant-in-training/Documents/Virtualenvs/AutoGrader_env/bin/python
export COMMIT=$TIP MUTANT_LOGS=$H/runs/h152_${TIP}_mutant_logs EXEMPT_EMAIL_DOMAINS= PYTHONDONTWRITEBYTECODE=1
ST=$H/runs/h152_${TIP}.status
WRAP="systemd-inhibit --what=idle:sleep:handle-lid-switch --who=GAP --why=GAP-test-run --mode=block systemd-run --user --scope -q -p MemoryMax=6G -p MemorySwapMax=0 nice -n 10 timeout -k 60 1800"
say() { echo "$(date +%H:%M:%S) $*" >> $ST; }
cd $WT || exit 9
[ "$(git rev-parse --short=8 HEAD)" = "$TIP" ] && [ -z "$(git status --porcelain)" ] || { echo "NOT FROZEN at $TIP"; exit 9; }
[ ! -e $MUTANT_LOGS ] && [ ! -e $H/runs/h152_${TIP}_baseline.log ] || { echo "logs of $TIP exist already"; exit 9; }
cp $H/tests_vf2_h152_probe.py $WT/$PROBE
cmp -s $H/tests_vf2_h152_probe.py $WT/$PROBE || { echo "probe copy differs"; exit 9; }
[ "$(git status --porcelain)" = "?? $PROBE" ] || { echo "unexpected files beside the probe"; rm -f $WT/$PROBE; exit 9; }
finish() {
  rm -f $WT/$PROBE
  rm -rf $WT/classrooms/__pycache__/tests_vf2_h152_probe*
  if [ -z "$(git -C $WT status --porcelain)" ] && [ "$(git -C $WT rev-parse --short=8 HEAD)" = "$TIP" ]; then say "probe removed; tree is the frozen tip again"; else say "WARNING: tree not the frozen tip at exit"; fi
}
trap finish EXIT
say "baseline start: loadavg $(cat /proc/loadavg)"
rc=0; $WRAP $PY -B $H/vf_h152_mutants.py baseline > $H/runs/h152_${TIP}_baseline.log 2>&1 < /dev/null || rc=$?
say "baseline end exit=$rc: loadavg $(cat /proc/loadavg)"
[ $rc -eq 0 ] || { echo "baseline red (exit $rc): no mutant is run"; exit 1; }
say "mutants start: loadavg $(cat /proc/loadavg)"
rc=0; $WRAP $PY -B $H/vf_h152_mutants.py > $H/runs/h152_${TIP}_mutants.log 2>&1 < /dev/null || rc=$?
say "mutants end exit=$rc: loadavg $(cat /proc/loadavg)"
echo "done: see $ST"
