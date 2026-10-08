#!/bin/bash
# v2, H-137, the SHORT REPEAT (r2) allowed by the Senior Manager: the three
# probe runs only, with the corrected probe; no scan, no comparison. The
# first run's files stay untouched beside these.
# What follows describes the first run's script, from which this is cut:
# v2's one slot for H-137 (0b's credential pattern check in the repository).
# No test runner, no database, no mutant: the three versions of the tool are
# the red proof (rule 19). Every step writes straight to its own file with
# stdin from /dev/null (rule 18); rules 12, 13, 16 wrap each step.
#   1. v2's probe on the tool of record (EXPECT=RECORD), on the first fix
#      46ef23ee (EXPECT=FIRST) and on the tip (EXPECT=CURED). Each must exit 0.
#   2. Whole-tree scans, `--all`, of each revision given: the tool of record,
#      the tool as it came into the repository (71b4fab3) and the tip's tool.
#   3. Comparisons: record against 71b4fab3 must be the same text; record
#      against the tip must lose no row.
# The tools are taken out of git into a temporary folder and checked by
# sha256; nothing is written into any checkout.
# Usage: vf_h137_run_r2.sh <tip-sha8>   (only after 0b's GRANT)
set -uo pipefail
TIP=$1
P=/home/bond-servant-in-training/Documents/Projects
REPO=$P/Grade-Automator-Plus
H=$P/GAP-v2-handover
RECORD=$P/GAP-0b-runs/credscan/credscan.py
PY=/home/bond-servant-in-training/Documents/Virtualenvs/AutoGrader_env/bin/python
OUT=$H/runs/h137_${TIP}_r2
ST=$OUT.status
WRAP="systemd-inhibit --what=idle:sleep:handle-lid-switch --who=GAP --why=GAP-test-run --mode=block systemd-run --user --scope -q -p MemoryMax=6G -p MemorySwapMax=0 nice -n 10 timeout -k 60 1800"
export PYTHONDONTWRITEBYTECODE=1
say() { echo "$(date +%H:%M:%S) $*" >> $ST; }
[ ! -e $ST ] && [ ! -e ${OUT}_logs ] || { echo "logs of $TIP exist already"; exit 9; }
mkdir -p ${OUT}_logs
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
[ "$(sha256sum $RECORD | cut -c1-16)" = "bdbd2e3d5b0e4b70" ] || { echo "the tool of record is not bdbd2e3d5b0e4b70"; exit 9; }
cp $RECORD $T/record.py
for pair in moved:71b4fab3 first:46ef23ee tip:$TIP; do
  git -C $REPO show ${pair#*:}:scripts/credscan.py > $T/${pair%%:*}.py || { echo "cannot read ${pair#*:}"; exit 9; }
done
say "tools: record $(sha256sum $T/record.py | cut -c1-16) moved $(sha256sum $T/moved.py | cut -c1-16) first $(sha256sum $T/first.py | cut -c1-16) tip $(sha256sum $T/tip.py | cut -c1-16)"
fail=0
for pair in record:RECORD first:FIRST tip:CURED; do
  tool=${pair%%:*}; expect=${pair#*:}
  say "probe on $tool (EXPECT=$expect) start: loadavg $(cat /proc/loadavg)"
  rc=0; TOOL=$T/$tool.py EXPECT=$expect $WRAP $PY -B $H/vf_h137_credscan_probe_r2.py > ${OUT}_logs/probe_${tool}.txt 2>&1 < /dev/null || rc=$?
  say "probe on $tool exit=$rc"
  [ $rc -eq 0 ] || fail=1
done
say "end: loadavg $(cat /proc/loadavg) fail=$fail"
echo "done: see $ST"
exit $fail
