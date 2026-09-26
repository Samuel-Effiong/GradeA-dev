#!/bin/bash
cd /home/bond-servant-in-training/Documents/Projects/GAP-flaky-r20-bab3d09
S=/tmp/claude-1000/-home-bond-servant-in-training-Documents-Projects-Grade-Automator-Plus/616c4f22-3b1e-4f87-8887-b22e4bacc0cb/scratchpad/r20/summary.txt
fp() { echo "HEAD $(git rev-parse HEAD)"; git status --porcelain --untracked-files=no | wc -l | sed 's/^/dirty_tracked_files /'; git ls-files -s | sha256sum | sed 's/^/index_fingerprint /'; }
{ echo "# r20 run of ConcurrentOverageDeliveryTests"; echo "# start $(date -Is)"; fp; } > $S
for i in $(seq -w 1 20); do
  L=/tmp/claude-1000/-home-bond-servant-in-training-Documents-Projects-Grade-Automator-Plus/616c4f22-3b1e-4f87-8887-b22e4bacc0cb/scratchpad/r20/run_$i.log
  s=$(date +%s)
  python manage.py test billing.tests.test_overage_purchase_integrity.ConcurrentOverageDeliveryTests --settings=settings_worktree --noinput -v 2 > $L 2>&1
  rc=$?
  e=$(date +%s)
  echo "run $i exit=$rc secs=$((e-s)) $(grep -E '^Ran ' $L) $(grep -E '^(OK|FAILED)' $L | tail -1) other_sessions=$(grep -c 'other session' $L) live_stripe=$(grep -c 'No such payment_intent' $L) log_sha256=$(sha256sum $L | cut -c1-16)" >> $S
done
{ echo "# end $(date -Is)"; fp; } >> $S
