# H-9 — Redis test isolation (regression fix): verification evidence

This record lives in the repo, not a scratchpad. Scratchpads are wiped
between sessions. Raw logs are in `docs/evidence/h9_redis_isolation/` and
listed in `SHA256SUMS.txt`.

**Status: VERIFIED.** All eight owner closure criteria are met (§5).

- The owner approved landing on beta subject to this verification.
- Under the owner's order (Section 9 gate → targeted verification →
  concurrent proof → H-1 stampede measurement → closure decision), the
  formal CLOSED decision follows the H-1 stampede measurement.

---

## 1. The defect

H-9 was fixed on 2026-09-12 by giving each test process its own key prefix
plus a prefix-scoped `clear()`. It regressed.

Twelve test modules that must run on real Redis wrote their own `CACHES`
override, and that override replaced the fix entirely:

- eleven used the unscoped `django_redis` backend on a fixed database
  number (3–15) with the shared `gaplus` prefix;
- `users/tests_activity_middleware_load` used it on the default database.

Their `cache.clear()` is FLUSHDB, so two concurrent test runs wiped each
other's cache entries and H-1 generation counters. Two further gaps:

- **Celery.** The broker and result backend shared one unprefixed keyspace
  across runs: queues, exchange bindings, and kombu's global `unacked` hash
  and index.
- **Server-wide statistics.** Two tests reset and read the Redis server's
  per-command statistics, which count every client's commands.

**Observed for real, 2026-09-14.** A Section 9 mutation run overlapped the
Section 8 strict gate for about 3 minutes. Section 8 aborted and re-ran, and
four sections had to queue their test runs serially.

## 2. The fix (branch `task/h9-redis-db-isolation`)

| Commit | What |
|---|---|
| `4820e33` | `AutoGrader.test_cache.real_redis_caches()`: a real-Redis override with the prefix-scoped backend and per-process prefix, used by all twelve modules. Under `manage.py test` the Celery broker and result backend get the same per-process `global_keyprefix`; production `visibility_timeout` is unchanged. Real-worker tests delete queues through kombu. Guards fail on the unscoped backend and on raw `CELERY_BROKER_URL` clients. |
| `438bb83` | Merge of beta `91f752b` (only `.secrets.baseline` conflicted; regenerated). |
| `a3acff9` | Owner's status, closure criteria and serial-runs rule recorded. |
| `042d575` | Broker acceptance test counts with `_size()` (LLEN). `SimpleQueue.qsize()` is a passive declare whose Redis `EXISTS` is not prefixed by kombu's `global_keyprefix`, so a prefixed queue read as missing. |
| `c4dddf1` | Server-wide statistics replaced with per-process command counting, plus a guard. **This commit's test failed.** It hooked `pack_command`, which redis-py 7.1.0 does not use for single commands. It was committed without gating on the test run — a process error, corrected next. |
| `9315f74` | Counter hooks `send_command` (single commands) + `pack_commands` (pipelines, which never call `send_command`), so each command is counted once. Gated: tests pass, and a SCAN mutant fails on the SCAN assertion itself. |

Kombu limitation, recorded for future tests: under `global_keyprefix`,
passive queue declares (existence checks) do not see prefixed queues.
Publishing, consuming, purging and `LLEN` are prefixed.

## 3. Verification runs

Infrastructure: PostgreSQL 18.6, Redis 8.0.5, Python 3.12.10, Django 5.2.6,
redis-py 7.1.0, kombu 5.5.4, celery 5.5.3.

**Host-quiet ordering.** Runs started only after Section 9's gate
finished. Every other session was told the host was in use, and held.

### 3a. Targeted modules on the fix (fresh DB, no `--keepdb`)

Scope: the 13 changed and isolation modules plus
`students.tests_grading_redelivery_live` and
`AutoGrader.tests_celery_signals`.

- **Result: 263 tests, 1 error, 1 skip.** No "other sessions" lines, and
  no leftover test DB.
- **The error** was the broker acceptance test's passive-declare bug, fixed
  in `042d575` and re-verified (§4).
- **The skip** is the CI-only guard `test_redis_is_required_when_ci_says_so`
  (`CI_REQUIRE_REDIS` unset). The module's 10 real Redis load tests ran and
  passed.
- **The Celery real-worker and broker tests: 18/18 ok** under the
  per-process broker prefix.

### 3b. Collision reproduction: old code vs fix

Setup: 4 rounds on each tree. Each round runs 2 processes simultaneously,
both running `AutoGrader.tests_cache_dashboard_freshness`, each on its own
test DB, sharing Redis.

| Tree | Failed runs | Failing tests |
|---|---|---|
| beta `91f752b` (old) | **5 / 8** | 9 failures across 6 tests, all in `tests_cache_dashboard_freshness`. They include a school generation reset by another run's flush ("a legacy wildcard sweep reset the school generation") and a probe key flushed mid-test ("None != 'cached'"). |
| fix | **0 / 8** | none |

Leftover `ag_h9_*` test DBs: 0.

### 3c. Owner acceptance proof: two FULL runs simultaneously

Setup: commit `9315f74` (tree `3ac24bb5`). Two detached worktrees, each
`git worktree lock`ed and clean (0 porcelain) before and after. Each suite
ran on its own fresh test DB (`test_ag_h9_overlap_a` / `_b`), with no
`--keepdb`, under `systemd-inhibit`. Both launched at 01:55:50 and the gate
ended at 02:33:08.

| | Side a | Side b |
|---|---|---|
| Tests | 4,153 — **OK** (skipped=20) | 4,153 — **OK** (skipped=20) |
| Duration | 2,217 s | 2,222 s |
| Exit code | **0** | **0** |
| FAIL / ERROR | 0 / 0 | 0 / 0 |
| "other sessions using the database" | 0 | 0 |
| Test DB destroyed / leftover DB / connections | yes / 0 / 0 | yes / 0 / 0 |
| HEAD after | `9315f74` (unchanged) | `9315f74` (unchanged) |
| Log (uncompressed) sha256 | `7d6e249b3918a4b09f25c7a850810b03ce0c88ca2835ae89b2e4d191f4596ac7` | `0dd0e4991e3158396478fea831b04fe340a876f3f41b83eebbb346530b43e6aa` |

Suspend events during the run: 0.

**Redis sampling during the overlap** (DB 0, keys per process prefix):

| Sample | Side a (`gaplus-t218704`) | Side b (`gaplus-t218708`) | Earlier finished runs' keys |
|---|---|---|---|
| 01:59:50 | 278 | 275 | unchanged |
| 02:03:50 | 10 | 6 | unchanged |
| 02:07:50 | 65 | 65 | unchanged |

Both namespaces were live at the same time throughout. Each run's count
rose and fell as it cleared only its own keys. Keys left by earlier
finished test processes were never removed by either live run. One prefix
dropped from 27 to 19 keys, which is 300 s TTL expiry of cache entries.

**The first attempt of this proof (commit `042d575`) did not pass.** Side b
was OK, but side a failed one test,
`test_bumping_never_issues_a_keyspace_scan` ("18 != 0"). It measured
through the server's shared statistics, so side b's SCANs were counted.
Data isolation held; the measurement did not. That run's exit codes were
also lost to a bug in the gate script (an undeclared associative array
under `set -u`), since fixed. Hence `c4dddf1`/`9315f74` and this re-run.

**Known, not a defect.** Generation counters are written without a TTL, by
H-1 design. So every finished test process leaves a few dozen counter keys
under its own prefix: 63 per suite here. That is litter in the development
Redis, not cross-run interference, and was already recorded under H-9's
"second finding".

## 4. Mutation evidence

Each mutant was applied to a checksum-backed copy, proven present, run, and
then restored and md5-verified. `git checkout` was never used.

| Mutant | Result |
|---|---|
| M1 rogue test module with the unscoped backend | guard FAILED, naming the file and line |
| M2 `real_redis_caches()` reverted to the unscoped backend | 3 failures: two-process same-fixed-DB acceptance test, config test, source-scan guard |
| broker `global_keyprefix` removed from settings | 2 errors: broker purge acceptance test, prefix config test |
| `bump_generation` issues a SCAN | no-SCAN test FAILED on its own assertion: "50 != 0: generation bumping issued a keyspace SCAN" |
| rogue module reading server-wide command statistics | guard FAILED, naming the file |

Clean re-runs after every restore passed.

## 5. Owner closure criteria (2026-09-14)

| # | Criterion | Met | Evidence |
|---|---|---|---|
| 1 | Targeted tests pass | yes | §3a (plus the corrected acceptance test re-verified in §4) |
| 2 | Two simultaneous full runs pass | yes | §3c |
| 3 | Isolated fresh DBs | yes | §3c, no `--keepdb`, both DBs created fresh |
| 4 | Real Redis | yes | §3, Redis 8.0.5 |
| 5 | Redis sampling shows simultaneous independent namespaces | yes | §3c sampling table |
| 6 | No cross-run deletion or contamination | yes | §3b fix 0/8; §3c both OK, other runs' keys untouched |
| 7 | Clean teardown, zero leaked DB connections | yes | §3c |
| 8 | Final regression remains clean | yes | §3c: two full-suite regressions of the committed tree |

## 6. Operational rule after this proof

The serial-runs restriction existed until the overlap proof passed. It has
now passed. The owner said that lifts the restriction, but only for test
runs from a tree that contains this fix (`4820e33` through `9315f74`).

**Code without the fix still runs the flushing suites**, so any session
branch that predates it must merge beta first. Until then its full-suite,
mutation and live-worker runs must still not overlap with anyone else's.
