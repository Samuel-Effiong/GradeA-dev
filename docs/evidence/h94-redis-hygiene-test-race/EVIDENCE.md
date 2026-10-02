# H-94: the Redis hygiene tests are race-free under --parallel

**Author:** d5. **Branch:** `task/h94-redis-hygiene-test-race`, off beta
`74bfc8d3`. Bundle 6. **Test only:** `AutoGrader/tests_redis_hygiene.py`.

## The change (4 points)
1. **Before:** beta's CI failed once on `74bfc8d3`:
   `SweepTests.test_delete_own_keys_takes_only_that_exact_prefix` found its
   lookalike key gone. The key sits under a DEAD pid's prefix. Any run that
   shares the test Redis, and under `--parallel` any other worker of the
   same run, may sweep a dead pid's prefix at any moment
   (`ProcessLifecycleTests` runs a real `sweep_dead_prefixes()`, and each
   child it spawns does a start-of-run sweep). Two tests asserted that such
   a key was still there:
   - `SweepTests`' delete_own_keys test (the lookalike, and the prefix under
     test itself);
   - `ProcessLifecycleTests`' SIGKILL test (the killed child's keys, between
     the kill and the sweep).
2. **After:** no test in the module asserts that a dead pid's key is still
   present.
   - The delete_own_keys test puts the prefix under test on a LIVE pid (a
     sibling process that never touches Redis), so no sweep removes it
     first. The lookalike stays on a dead pid, and the test no longer asks
     whether it survived: it records which keys `delete_own_keys` itself
     unlinked, and expects exactly the prefix's two.
   - The SIGKILL test waits for the killed child without reaping it. A
     zombie's pid still exists, so every sweep takes it for a live run and
     leaves its keys alone until the test has looked at them. Then it reaps
     the child and sweeps.
   - The module docstring states the rule for future tests.
3. **Reach:** the test module only. Nothing is serialised and no production
   file changes.
4. **Tests that show the race is closed:** two tests run a real sweep from
   another process in the middle of each of those tests
   (`test_delete_own_keys_is_unmoved_by_another_runs_sweep`,
   `test_sigkill_leaves_keys_whatever_another_run_sweeps_meanwhile`). That
   makes the CI interleaving deterministic. They fail on the old test
   bodies and pass on the new ones.

**The reasoning, assertion by assertion.** After the change the module's key
assertions are of four kinds, and none can be broken by another run's sweep:
- a key under a live pid is present (a sweep skips live pids, and re-checks
  liveness right before it removes a prefix);
- a key under this process's own pid, or outside the `gaplus-t<digits>:`
  shape, is present (a sweep skips its caller's pid in its own process, a
  live pid in any other, and never matches the other shapes);
- a dead pid's keys are gone after this test's own sweep (true whoever
  swept them);
- what `delete_own_keys` unlinked (recorded from the call, not read back
  from Redis).

| Commit | What |
|---|---|
| `ae773cb9` | the two tests with a foreign sweep mid-test (red on the old bodies) |
| `8c2d0d46` | the race-free bodies |
| `2d04fcaf` | one assertion of mine corrected (see "The red first attempt") |

## Gates
Under 0b's grants, on the frozen tip `2d04fcaf`. Status: `chain.status`.

| Gate | Result | Log |
|---|---|---|
| Repro at `ae773cb9` (h78-repro worktree): the module | 14 tests, 2 failures: exactly the two new tests | `repro_ae773cb9.log` |
| The module with `--parallel 4`, five times in a row, under the full-suite lock | 14 OK, five times | `module_parallel4_run1..5_2d04fcaf.log` |
| The 9 guards | 88 OK | `guards_2d04fcaf.log` |

No mutation battery: there is no production change. The module has four
test classes, so `--parallel 4` gives each its own worker; the logs do not
print the worker count.

**The red first attempt (`8c2d0d46`).** The first parallel run failed in
both delete_own_keys tests (`module_parallel4_run1_8c2d0d46_red.log`,
`chain_8c2d0d46_red.status`). It was my mistake, not the race: an assertion
I had added expected exactly one key under the live prefix in database 0,
and there were two. `2d04fcaf` checks that the key is present. The gate was
then run again from the start.

**The two Redis layouts (0b's question).** The test puts one key "in
database 15" (`db=15`).
- *The cache URL names a database* (`redis://host:6379/0`): redis-py takes
  the database from the URL and ignores the `db=` argument (checked on
  redis 7.1.0), so that key lands in database 0 with the others. This is the
  local layout and, by `.github/workflows/tests.yml` (`REDIS_LOCAL_URL:
  redis://localhost:6379/0`), CI's too. The first attempt's assertion
  assumed the other layout and failed here.
- *The URL names no database* (`redis://host:6379`): `db=15` is honoured and
  the key lands in database 15.
The corrected assertions hold in both: the key under test is present in
database 0 (`assertIn`, not an exact list); `delete_own_keys` returns 2;
nothing is left under the prefix in database 0 or 15; and the keys it
unlinked are exactly the two. Only the first layout was run; the second is
by reading.

**Found on the way (not changed here; a row is proposed to the SM).** In
the first layout, `redis_test_hygiene._clients()` builds its 16 "per
database" clients from that URL with `db=`, so all 16 are on the URL's
database. The sweep and `delete_own_keys` therefore cover only that one
database. Today the cache, the Celery broker and the result backend all use
the same URL, so nothing is missed; the code and its comments claim more
than it does.

**How the runs were made.** Every run used `--settings=settings_worktree`
and an empty `EXEMPT_EMAIL_DOMAINS`, wrapped as `systemd-inhibit
--what=idle:sleep:handle-lid-switch … --mode=block systemd-run --user
--scope -p MemoryMax=6G -p MemorySwapMax=0 nice -n 10 timeout -k 60 1800`
(rules 12, 13, 16); the parallel runs under `flock
~/.machine-fullsuite.lock`.
