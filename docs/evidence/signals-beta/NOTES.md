# Signals sort and filter, beta (brief 4, SM -31 (was -16), 2026-11-11): run notes

Owner approved for beta (2026-11-11), the same bug-fix path as brief 3. Base
origin/beta 44904774a (includes the user-list port), branch
task/signals-beta, worktree Grade-Automator-Plus-signals-beta, test DB
test_signals_beta. Environment for every run: venv
~/Documents/Virtualenvs/h284_deps_env, --settings=settings_worktree,
--keepdb, --noinput, under flock ~/.machine-fullsuite.lock.

### M0 (survey, declared): query counts on beta's base

A scratch test (copied into billing/ for the run, deleted after, never
committed) with N teacher BetaProfiles (N = 5 and 30), a super admin client,
cold cache:
- GET analytics beta/intent-signals at page_size 5 and 25;
- GET intent-signal-distribution with 5 and with 30 profiles;
- GET /api/v1/users/ at page_size 5 and 25.
Expected:
- intent-signals is fixed (count + page select_related("user"); the flags
  are computed in Python from columns), 5 = 25;
- distribution is fixed (one select_related query), 5 = 30;
- the user list is fixed at 13 (M1 of brief 3).
Exact numbers are not predicted beyond the user list's 13.

M0 result: intent-signals 9 = 9 (page 5/25), distribution 8 = 8 (5/30 profiles), user list 13 = 13, as predicted (all already fixed). Log M0_before_count.log; the scratch test was deleted.
