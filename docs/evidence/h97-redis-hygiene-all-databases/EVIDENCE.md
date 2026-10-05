# H-97: the Redis test hygiene really visits each of the 16 databases

**Author:** d5. **Branch:** `task/h97-redis-hygiene-all-databases`,
base-updated by 0b onto `task/beta-batch-6` `93cb8648` (`2d64b9fd`); gated
tip `a4f379be`. Test infrastructure only: `AutoGrader/redis_test_hygiene.py`
is never imported by production code.

## The change (4 points)
1. **Before:** `redis_test_hygiene._clients()` built its "one client per
   database" with `redis.Redis.from_url(location, db=n)`. When the URL names
   a database (`redis://host:6379/0`: the local layout and, by
   `.github/workflows/tests.yml`, CI's) redis-py takes the database from the
   URL and ignores `db=` (checked on redis 7.1.0). All 16 clients were on
   the URL's database, so the teardown (`delete_own_keys`) and the dead-pid
   sweep covered that one database only.
   - The cache, the Celery broker and the result backend use that URL, so an
     ordinary test's keys were covered.
   - Test modules that put a real-Redis cache on another database with
     `real_redis_caches("redis://127.0.0.1:6379/<n>")` (11, 12, 14 and 15 in
     the code today) left their `gaplus-t<pid>:` keys behind for good.
     Generation counters have no TTL.
   - **The wrong-result path (SM):** a later test process that is given one
     of those pids has the same prefix and reads the stale keys, unless its
     test clears the cache first (most of those classes do, in `setUp`).
     With `pid_max` 4,194,304 and about 118 stale prefixes that is roughly 3
     in 100,000 per process: rare, and unreproducible when it happens.
2. **After:** `location_for(location, db)` returns the URL pointed at that
   database (the path of a `redis://` or `rediss://` URL, the `db` parameter
   of a `unix://` one), and `_clients()` calls `from_url` on it. Each of the
   16 clients is on its own database. The rule the sweep applies is
   unchanged: only `gaplus-t<digits>:` keys, only for pids that are not
   running, never the caller's own pid.
3. **Reach:** every test run's start-of-run sweep, teardown and end-of-run
   sweep now cover databases 0 to 15. No production code, no test's result.
4. **Tests:** `AutoGrader/tests_redis_hygiene_databases.py` (new module):
   each client is on its own database; a key put through one client is not
   seen through another; a dead pid's key in another database is swept and
   a live pid's is left; `delete_own_keys` reaches another database; other
   key families in another database survive both the sweep and
   `delete_own_keys`; a whole other run's sweeps and teardown leave this
   process's and a live sibling's keys alone in databases 11, 12, 14 and
   15; the next run's start-of-run sweep removes a dead run's keys (the
   pid-reuse case, as far as a test can show it); and `location_for` changes
   only the database for eight URL shapes.
   Following H-94's rule, a dead pid's key is only ever asserted to be gone.

**Also here (v2's note from the H-94 verification, SM ruling):**
`test_sweep_removes_dead_prefixes_and_nothing_else` used two fixed key
names, so two separate runs on one Redis could delete each other's copy
between the put and the assertions. The names now carry the process id and
keep their shapes; the test asserts the second still does not parse as a
pid prefix (`60415c99`).

| Commit | What |
|---|---|
| `5d8af9f4` | the tests (red) |
| `ca54c037` | the first fix (`client_for`: parse the URL, set the database, build the client) |
| `94d26924` | the mutation runner |
| `78399ff9` | 0b's conditions as tests: other key families survive; a live run's keys in databases 11–15 survive another run |
| `60415c99` | v2's note: per-process names for the sweep test's two bystander keys |
| `2d64b9fd` | base update onto `93cb8648` (0b), which has H-94 |
| `a4f379be` | the fix reworked to keep one raw-client site (`location_for`); mutants D1–D4 |
| `f1e0e9d7` | v2's finding: a socket URL keeps its credentials; 19 URL shapes; mutant D5 |

## The one-off sweep of the leftovers
Once the clients are on their own databases, the first test run with the
fix sweeps the leftovers by itself. The SM ruled that this run IS the
one-off sweep, with read-only listings as the record.

- **Told first:** the Vezi project shares this laptop. Their senior manager
  (session vezi-95) replied before any run: "Vezi does not use Redis at all:
  I checked the backend's settings, dependencies and code and found no
  reference to it (our cache and queue are in PostgreSQL). No Vezi key
  exists under 'gaplus-t' or any other name, so there is no objection from
  our side to your cleanup."
- **It happened in the first gate**, in run 1 at about 16:21 on 2026-10-02
  (`first_gate_2d64b9fd/`):

| Listing (read-only: SCAN and a /proc check) | Dead-pid `gaplus-t` keys | Where |
|---|---|---|
| `sweep_listing_1611.txt` (16:11) | 186 | database 3: 3; 6: 12; 14: 115 (115 prefixes); 15: 56 |
| `first_gate_2d64b9fd/listing_before.txt` (16:17:30) | 186 | the same |
| `first_gate_2d64b9fd/listing_after.txt` (16:21:36) | 0 | |

  `listing_compare.txt`: every other key family has the same count in
  databases 1–15 before and after (`secreplay…`, `secbill…`,
  `verifyreplay…`, `oauthreplay…`: old security-evidence keys, not H-97's
  to delete; the row says to decide about them separately).
- **Database 0 in those listings:** 260 keys of one live pid before, none
  after. That was ed's regression, which held the full-suite lock; this
  run waited behind it (wall 246 s for 13 s of tests), so ed's run had
  ended and removed its own keys by then. A sweep cannot remove a live
  pid's keys. `other_runs_at_first_run.txt` lists the runs that were active.
- **In this gate** (the re-gate) a fresh listing was taken before and after
  the first run: `listing_before.txt`, `listing_after.txt`,
  `listing_compare.txt`: one dead-pid key before, in database 14, none after; every other key family unchanged in databases 1–15. That one key was left in the 45 minutes since the first gate by a run of another branch, which does not have this fix yet: the leak continues on every branch until H-97 is merged. The listing and comparison scripts are `listing.py.txt` and `compare.py.txt`.

## Gates
Under 0b's grants. Status: `chain.status`.

| Gate (on `a4f379be`) | Result | Log |
|---|---|---|
| Repro at `5d8af9f4` (h78-repro worktree): the new module on the old code | 6 tests, 16 failures (15 are per-database subtests): red as expected | `repro_5d8af9f4.log` |
| The three Redis modules (`tests_redis_hygiene_databases`, `tests_redis_hygiene`, `tests_redis_test_isolation`) at `--parallel 4` under the full-suite lock, three times | 36 OK (skipped=2), three times | `module_parallel4_run1..3_a4f379be.log` |
| Mutants D1–D4 (`test_h97_mut`, rule 17) | baseline green, 4/4 killed, every restore sha-verified | `b_mutation_battery_a4f379be.log`, `logs/`, `results.tsv` |
| The 9 guards plus the brief's repo-wide list | 129 OK | `guards_a4f379be.log` |

**The first gate (`2d64b9fd`, superseded).** Repro red as expected; the
three modules green three times; 3/3 mutants killed; the guards step RED,
one failure:
`AutoGrader.tests_cache_invalidation_coverage.RawRedisClientTests.test_every_raw_client_user_is_listed`.
H-73's guard counts the places a module obtains a raw Redis client and
compares them with a registry; `client_for` had added a second one in
`redis_test_hygiene.py`. `a4f379be` keeps the single `from_url` call and
leaves the registry unchanged. The behaviour is the same as the first
version's. Logs: `first_gate_2d64b9fd/`. One slip of mine: I deleted that
gate's per-mutant log files while clearing the worktree for the rework; its
battery summary log is kept there.

**An earlier false start.** My first attempt to start the first gate ran
nothing: the script was not executable.

**How the runs were made.** Every run used `--settings=settings_worktree`
and an empty `EXEMPT_EMAIL_DOMAINS`, wrapped as `systemd-inhibit
--what=idle:sleep:handle-lid-switch … --mode=block systemd-run --user
--scope -p MemoryMax=6G -p MemorySwapMax=0 nice -n 10 timeout -k 60 1800`
(rules 12, 13, 16); the parallel runs under `flock
~/.machine-fullsuite.lock`. Rule 17: the battery and its baseline ran with
`PYTHONDONTWRITEBYTECODE=1`, and the runner deleted `AutoGrader/__pycache__`
in its worktree before the baseline, before each mutant and after each
restore.

## After v2's static read (delta at `f1e0e9d7`)
v2's finding, ruled in by the SM: `location_for` rebuilt a `unix://` URL
from its path alone, so the credentials of a password-protected socket were
dropped and the hygiene clients would have failed to authenticate there
(the cleanup would have stopped silently, since hygiene is best effort).

`f1e0e9d7`: the unix branch keeps the network location, and blank-valued
parameters are kept as written. `test_the_database_is_set_whatever_the_url_says`
now has 19 shapes: v2's nine, a socket with a user and a password, and
more; eight carry credentials. Per the SM, a failure names the shape and
the database and never prints a URL or a parsed value, and no log or
evidence file here holds a URL with a password (the test's made-up password
does not appear in any committed log; checked with grep).

| Delta gate (rule 15.4, SM ruling; under 0b's grant) | Result | Log |
|---|---|---|
| `AutoGrader.tests_redis_hygiene_databases` and `AutoGrader.tests_cache_invalidation_coverage` (H-73's raw-client guard) | 29 OK | `delta_modules_f1e0e9d7.log` |
| Mutant D5: the network location dropped again in the unix branch | killed, by the three socket shapes that carry credentials; restore sha-verified, rule 17 | `delta_mutant_D5_f1e0e9d7.log`, `logs/D5.log` |

The three parallel runs, the other mutants and the guards were not re-run:
they stand from the re-gate at `a4f379be`.

## Mutants
| Id | Guards | Result |
|---|---|---|
| D1 | the database goes into the URL's path | killed |
| D2 | the clients are not built with `from_url(location, db=db)` | killed |
| D3 | all 16 databases are visited | killed |
| D4 | a `unix://` URL's database is its `db` parameter | killed |
| D5 | a socket URL keeps its credentials (v2's finding; added at `f1e0e9d7`) | killed |

## For the verifier (v2's three points)
1. **Only our dead prefixes, in every database:**
   `test_other_key_families_in_another_database_survive` and
   `test_another_runs_sweeps_leave_a_live_runs_keys_in_other_databases`.
   The hygiene module has no database-wide command: it scans
   `gaplus-t*:*`, parses `^gaplus-t(\d+):` and unlinks the keys it listed.
2. **The one-off sweep:** the listings above; the after listing differs
   from the before listing only by `gaplus-t<dead pid>:` keys.
3. **Both URL layouts:** `test_the_database_is_set_whatever_the_url_says`
   covers 19 shapes: a URL that names a database, one that names none,
   other parameters, a `?db=` parameter, an IPv6 host, credentials (plain
   and percent-encoded), `rediss://`, and `unix://` with and without
   credentials. Only the
   first layout exists on this machine, so the tests that talk to Redis ran
   in that one.
