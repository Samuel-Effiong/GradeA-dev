# User list sorting, beta port (brief 3, SM -16, 2026-11-11): run notes

Owner approved for beta as a bug fix (2026-11-11). Base origin/beta
2c8165b69, branch task/user-list-beta, worktree
Grade-Automator-Plus-user-list-beta, test DB test_user_list_beta.
Environment for every run: venv ~/Documents/Virtualenvs/h284_deps_env,
--settings=settings_worktree, --keepdb, --noinput, under flock
~/.machine-fullsuite.lock.

### M0 (survey, declared): the list's query count on beta's base

The scratch test (scratchpad qcount_test.py, copied into users/ for the run
and deleted afterwards, never committed) runs the super admin list with 30
teachers that have wallets, at page_size 5 and 25. users/views.py,
users/serializers.py and billing/serializers.py are byte-identical to the
staging base aeb8b157e, where 25 rows cost 309 queries.
Expected: 25 rows = 309. 5 rows not predicted. The test DB is created on
this first run.

M0 result: 5 rows = 69 queries, 25 rows = 309 (as predicted for 25). Log
M0_before_count.log. The scratch test was deleted and not committed.
