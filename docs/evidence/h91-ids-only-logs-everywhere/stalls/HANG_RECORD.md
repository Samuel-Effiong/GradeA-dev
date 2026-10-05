# H-91 gate (c) on b6fbdbea: the run stalls at its end, twice (2026-10-02)

Label list: billing users classrooms assignments + AutoGrader.tests_beat_locks,
tests_management_commands_are_commands, tests_no_pii_in_logs,
tests_no_wildcard_invalidation, tests_cache_invalidation_coverage,
tests_migration_rollback_defaults, tests_redis_test_isolation,
tests_beat_health + the four sweep/schema guards; --parallel 2
--verbosity 2, under the full-suite lock, 6G. 3878 tests found.

| | Run 1 (17:29:47) | Run 2 (17:45:41, PYTHONFAULTHANDLER=1) |
|---|---|---|
| ok / FAIL / ERROR / skipped when it went silent | 3746 / 0 / 0 / 17 | 3745 / 0 / 0 / 17 |
| last output | 17:36:39 | 17:54:02 |
| last test line | assignments.tests_title_sanitization ... ok | the same |
| AutoGrader modules that reported | tests_beat_health (16), tests_beat_locks (5 of its TestCase tests) | the same |
| ended | I aborted the workers at 17:41 (parent's traceback lost) | SIGABRT parent 17:57:10, then workers; exit 134 |

Never reported, both runs (about 116 tests): the rest of tests_beat_locks
(its SimpleTestCase classes) and all of tests_management_commands_are_commands,
tests_no_pii_in_logs, tests_no_wildcard_invalidation,
tests_cache_invalidation_coverage, tests_migration_rollback_defaults,
tests_redis_test_isolation.

## Facts captured in run 2, before any signal
- Process tree (hang2_tree_*.txt, workers_at_start_rerun.txt): parent
  1460464 and workers 1461319, 1461321: the SAME pids as one minute into
  the run. No other descendant, no zombie, no Chromium, no `python -c`
  child.
- Parent: 1 thread, sleeping in do_wait. Workers: 1 thread each, sleeping
  in futex_do_wait.
- pg_stat_activity (hang2_pg_activity.txt): no connection at all to this
  worktree's test databases; 0 ungranted locks.
- The lock (hang2_lock.txt): held by this run's flock only; nothing queued.
- Journal 17:53-17:58: no OOM kill, no segfault.

## Tracebacks (hang2_tracebacks.txt)
- PARENT: multiprocessing/util.py `_exit_function` -> `_run_finalizers` ->
  pool.py:732 `_terminate_pool` -> process.join -> popen_fork.poll.
  The parent is in INTERPRETER EXIT (multiprocessing's atexit hook),
  joining pool workers that have not exited. It is not in the test loop.
  No "Ran N tests" line and no traceback was printed before it.
- BOTH WORKERS: pool.py:114 `worker` -> queues.py:386 `get` ->
  synchronize.py:95 `__enter__`: idle, waiting for the task queue's lock.

## What that shows, and what it does not
- Shown: the parent left Django's parallel result loop without finishing
  and without printing anything, and began to exit; the pool's workers
  did not die when the pool was terminated, so the exit waits for ever.
  Two separate defects: (1) why the parent leaves the loop silently,
  (2) why the workers survive terminate.
- A lead, NOT proven: AutoGrader/redis_test_hygiene.py installs a SIGTERM
  handler in the parent that raises SystemExit (silent, no traceback) and
  restores the old handler when the run's `with` block ends. The parent's
  caught-signal mask at the stall had no SIGTERM (handler already
  restored: consistent with having left the block); the forked workers'
  masks still had it. What would send the parent a SIGTERM I have not
  found: no os.kill/killpg in the tested apps apart from the pid check.
- Not the cause: a lost or replaced worker, a leftover grandchild, a
  database lock, OOM, suspend, the H-91 guard module (1a's read; and it
  never started).
- Deterministic on this label list and tip: same point twice.
