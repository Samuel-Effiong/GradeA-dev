# Verification: H-94, the Redis hygiene tests are race-free under --parallel (d5)

- **Branch:** task/h94-redis-hygiene-test-race at **5eb85fbc**, off beta 74bfc8d3. The code tip is 2d04fcaf; the two commits after it are docs only.
- **Change:** test-only, AutoGrader/tests_redis_hygiene.py (+76/-5). No production file.
- **Commits:** ae773cb9 (two tests that run a real sweep from another process mid-test), 8c2d0d46 (the race-free test bodies), 2d04fcaf (one assertion corrected).
- **Verifier:** v2 (independent), 2026-10-02.
- **Verdict:** **VERIFIED-WITH-NOTES**

## Static read
- **The race.** Two tests asserted that a key under a DEAD pid's prefix was still present. Any run sharing the test Redis, and under `--parallel` any other worker of the same run, may sweep a dead pid's prefix at any moment. That is beta CI's one failure (the lookalike key was gone).
- **`delete_own_keys` test, after the fix:**
  - The prefix under test belongs to a live sibling process that never touches Redis, so no sweep removes its keys first.
  - The lookalike key stays under a dead pid. The test no longer asks whether it survived; it records the keys `delete_own_keys` itself unlinked (`patch.object(hygiene, "_unlink", wraps=...)`) and expects exactly the prefix's two.
  - The point of the test is kept: pid 12's cleanup must not match pid 123's keys.
- **SIGKILL test, after the fix:** it waits for the killed child with `os.waitid(..., WEXITED | WNOWAIT)`, without reaping it. `pid_is_alive` is `os.kill(pid, 0)`, which succeeds for a zombie, so every sweep leaves the keys alone until the test has looked. The test asserts `pid_is_alive(child.pid)` itself, then reaps and sweeps.
- **The other tests in the module:** none asserts that a dead pid's key is present. `test_sweep_removes_dead_prefixes_and_nothing_else` asserts a dead pid's keys are gone after its own sweep, which holds whoever swept them.
- **Rule 14:** the one mock wraps the real `_unlink`, so real values are returned and nothing reaches a response.
- **No mutation battery:** there is no production change, so rule 17 does not apply.

## d5's gates (read)
| Gate | Result | Log |
|---|---|---|
| Repro at ae773cb9 | 14 tests, 2 failures: exactly the two new tests | repro_ae773cb9.log |
| The module with `--parallel 4`, five times, under the full-suite lock | 14 OK, five times | module_parallel4_run1..5_2d04fcaf.log |
| The 9 guards | 88 OK | guards_2d04fcaf.log |

Disclosed by d5: the first attempt (8c2d0d46) was red on d5's own assertion, which expected one key in database 0 and found two. Fixed test-only at 2d04fcaf, and the gate was run again from the start.

## v2 runs (0b's grant, rules 16, 13 and 12, scratch worktree, `--settings=settings_worktree`)
| Run | Result | Log (GAP-v2-handover/runs/) |
|---|---|---|
| The module once, serial, at ae773cb9 (the new tests on the old bodies) | **Ran 14, FAILED (failures=2)**: exactly the two new tests; the other 12 ok | h94_repro_ae773cb9.log |
| The module with `--parallel 4` at 5eb85fbc, run 1, under `flock ~/.machine-fullsuite.lock` | **14 OK** | h94_parallel4_run1_5eb85fbc.log |
| Run 2 | **14 OK** | h94_parallel4_run2_5eb85fbc.log |
| Run 3 | **14 OK** | h94_parallel4_run3_5eb85fbc.log |

- **The two new tests really fail on the old code,** for the right reasons:
  - `test_sigkill_leaves_keys_whatever_another_run_sweeps_meanwhile`: the killed child's keys are already gone (`[] == []`), because the reaped child's pid is dead to the other run's sweep.
  - `test_delete_own_keys_is_unmoved_by_another_runs_sweep`: `0 != 2`, because the dead pid's prefix was swept before `delete_own_keys` ran.
- By 0b's account, ed's 6G gate was running beside these runs on the same Redis, which is the condition the fix is for.

## H-97 (the SM's question): the fixed tests do not depend on it
- H-97: where the cache URL names a database, redis-py ignores `db=`, so the hygiene code's 16 "per database" clients are all on the URL's database. Six test modules write to other databases through `real_redis_caches("redis://…/<n>")`, which the sweep never reaches.
- **tests_redis_hygiene.py uses no such cache.** It writes through the cache URL with `db=` arguments only.
- The fixed assertions hold in both layouts:
  - URL names a database (local and CI): the `db=15` key lands in the URL's database; `delete_own_keys` returns 2 and unlinks exactly the two keys.
  - URL names none: the key lands in database 15, which a real `db=15` client reaches; the same assertions hold. This layout is by reading only; it was not run, by d5 or by v2.
- Leftover keys of dead pids in other databases (H-97's finding) cannot touch these tests: they read database 0 and their own `db=` clients only.

## Notes (none blocks)
1. **A residual race between two separate RUNS, older than H-94 and outside it.** `test_sweep_removes_dead_prefixes_and_nothing_else` uses two fixed key names (`secreplay-hygiene-test:1:x`, `gaplus-tnotapid:1:x`). If two runs on the same Redis execute that test at the same moment, one run's teardown can delete the other's copy before its "sweep touched …" assertion. The window is a few milliseconds. Worth a LOW backlog line (a per-run suffix on the two names).
2. **`--parallel 4` gives each of the module's four test classes its own worker;** the logs do not print the worker count. d5 says so too.
3. **H-97 is real and separate** (MEDIUM, per d5's correction): the sweep misses keys in other databases. Not changed here.
4. **The zombie technique is Linux-specific** (`os.waitid`, `WNOWAIT`). The project's tests already assume Linux.
