# H-2 — test teardown / leaked connections — verification evidence

Kept in the repo on purpose. Scratchpad logs get wiped between sessions,
and evidence that can't survive a session restart doesn't count as evidence.

**This is NOT the definitive release gate.** These runs are snapshots of a
dirty working tree shared by several sessions. The definitive gate is a fresh
run from the final committed tree (H-1 Stage 3, item 6).

---

## 1. Root cause (corrected)

The first diagnosis in `docs/HARDENING_BACKLOG.md` was **wrong on two counts**.
Both are recorded here so nobody re-applies either fix.

| Hypothesis | Tested how | Result |
|---|---|---|
| Persistent connections (`CONN_MAX_AGE=600`) keep sessions open, so set `CONN_MAX_AGE=0` in test mode | Added the setting, full run | **Still 13 sessions, exit 1.** Reverted. |
| Promote a `CloseConnectionsMixin` that calls `connections.close_all()` in `tearDown`, and adopt it in the five threaded suites | Adopted in four suites, full run, plus a direct probe | **Ineffective.** Django's connection handler is *thread-local*: `close_all()` in the main thread cannot close a connection opened by a worker thread. Reverted, and those four files are back to their committed state. |
| `assignments/tests_security.py` `ConcurrentAccessRevocationTest.test_no_download_succeeds_after_the_withdrawal_commits` spawns **13** threads (`hammer` ×12, `revoke` ×1), and none of them closes its own connection | Counted the threads against the "13 other sessions" in the teardown error; ran that file alone | **Confirmed.** 13 threads, 13 leaked sessions, and the file alone reproduces the non-zero exit. |

**Fix:** each of `hammer()` and `revoke()` now wraps its body in
`try: … finally: connection.close()`, so every thread closes the connection it
opened. That one file is the only source change for H-2. The other suites the
backlog named (`users/tests_activity_middleware_load.py`,
`users/tests_login_lockout.py`, `students/tests_grading_idempotency.py`,
`assignments/tests_load.py`) were **not** the leak, and they are unchanged.

## 2. Before / after — isolated file

| | Before fix | After fix |
|---|---|---|
| `assignments.tests_security` | tests OK, teardown `There are 13 other sessions using the database`, **exit 1** | **58 tests OK, exit 0**, no session warning |

## 3. Full cross-app regression — gate3

| | |
|---|---|
| Command | `python manage.py test AutoGrader classrooms users students assignments dashboard --noinput -v 1` |
| Branch / HEAD | `beta` / `06516cf49960a309ec2a607c797310b4830c51c5` |
| Tree fingerprint (start) | `a97e191702b66588bfcffceafa4765e1` |
| Tree fingerprint (end) | `db1bef987fb7a08fcf2b666f9815fd41` — see §3a |
| Database | real PostgreSQL, isolated `test_ag_gate3` |
| Redis | real Redis, per-process key prefix (H-9 `PrefixScopedRedisCache`) |
| Result | **Ran 1804 tests in 842.018s — OK (skipped=13)** |
| Exit code | **0** |
| Teardown | `Destroying test database for alias 'default'...` succeeded. No "other sessions" line anywhere in the log. |
| Leftover DB / sessions afterwards | 0 / 0 (`pg_database`, `pg_stat_activity`) |
| Redis errors in log | 77 lines of `redis.exceptions.ConnectionError: redis unreachable`. All of them are **injected on purpose** by `classrooms/tests_concurrency_and_resilience.py:286,382` (`side_effect=RedisConnectionError("redis unreachable")`), and those tests passed. None are real outages. |

Fingerprint = `md5( git diff HEAD ‖ sorted md5sum of every untracked file )`.

> **Fingerprint defect (found in gate4/gate5 logs, 2026-09-13).** The helper
> piped `git ls-files` through `xargs` without NUL separation, so the six
> untracked files under `docs/backend/phase 2/` (paths with spaces) were
> never hashed. The md5 values in this document are deterministic, but
> they do not cover those six files. None of them is code. Future gates
> must use the NUL-safe form already used in `SECTION_4_GATE_EVIDENCE.md`:
> `{ git diff HEAD; git ls-files --others --exclude-standard -z | sort -z | xargs -0 -r sha256sum; } | sha256sum`.
> The tree as it stood after the failure simulation, under that form:
> `81dd69cfa10972d0a0f05fdcc92f4e88c5e4671a8ab19977217ec785180bb213`.

### 3a. The tree moved during the run

The only files modified after the run started (mtime ≥ 17:26) were
`ai_processor/services.py`, `ai_processor/tests_answer_chunk_merge.py`,
`ai_processor/tests_answer_benchmark_scenarios.py` and
`ai_processor/tests_answer_benchmark_failures.py`. A concurrent session edited
them. None of them is an H-2 file. `ai_processor` was not among the apps under
test, and modules are imported once at test discovery. So the result covers
the H-2 change, but it is **not** a certificate for that later tree state.

## 4. Repeatability — three consecutive clean full runs

Same command, same apps, HEAD `06516cf4`, each run on its own isolated test DB.
Leftover sessions and databases were checked with `pg_stat_activity` /
`pg_database` right after each run.

| Run | Start → end (+01:00) | Tree FP start → end (md5, see §3 defect) | Result | Exit | "other sessions" | Leftover sessions / DBs |
|---|---|---|---|---|---|---|
| gate3 | 17:26 → ~17:41 | `a97e1917…` → `db1bef98…` | 1804 OK (13 skipped), 842.0s | **0** | none | 0 / 0 |
| gate4 | 17:48:05 → 18:01:34 | `e637f81e…` → `673ecc1f…` | 1804 OK (13 skipped), 793.6s | **0** | none | 0 / 0 |
| gate5 | 18:01:35 → 18:16:46 | `673ecc1f…` → `cb63238a…` | 1804 OK (13 skipped), 897.9s | **0** | none | 0 / 0 |

`FAIL:` / `ERROR:` lines in gate4 and gate5: 0 and 0.

**Why the tree moved during each run.** Files modified during gate4+gate5:
`ai_processor/services.py`, `ai_processor/tests_answer_benchmark_concurrency.py`
and `ai_processor/benchmark/answers/documents.py`, all from a concurrent
session, plus this document and `docs/HARDENING_BACKLOG.md`, which were
written during gate4. No H-2 file changed, and `assignments/tests_security.py`
was not touched during any run.

## 5. Failure simulation — the leak is detected, not tolerated

Mutant: in both `hammer()` and `revoke()`, `finally: connection.close()` was
replaced with `finally: pass` (2 sites, AST-checked). The run was
`manage.py test assignments.tests_security` on an isolated DB.

| | Fixed file | Mutant |
|---|---|---|
| Tests | 58 OK | **58 OK** — every test still passes |
| Teardown | clean | `DETAIL: There are 13 other sessions using the database.` |
| Exit code | 0 | **1** |

The mutant still passes every test. That is exactly the defect H-2 describes,
and it proves the non-zero exit comes from those two `close()` calls and
nothing else. The file was restored from a file copy (not `git checkout`)
and verified by md5: `4db607818b3a23b500584996a375f64b` before and after.
The mutant's orphaned `test_ag_h2sim` was dropped afterwards.

## 6. Acceptance

- [x] Full suite exits **0** (gate3, gate4, gate5)
- [x] Three consecutive clean full runs (§4)
- [x] No lingering `test_*` connections in `pg_stat_activity` after a run (§4)
- [x] Failure simulation: a deliberately leaking test is detected (§5)
- [x] Before/after measured (§2, §5)
- Adversarial / stress / live-stack: **not applicable.** This is a test-harness
  defect with no production code path. Its only runtime surface is the test
  runner's teardown.

**H-2: Closed — fixed, verified with three consecutive clean full runs and
failure/mutation simulation.** (Owner sign-off 2026-09-13.)

The owner reviewed the fingerprint defect in §3 and does not consider it
grounds to invalidate these runs: the omitted files are documentation, not
code. Every future gate uses the NUL-safe sha256 fingerprint.

Closing H-2 does not complete the repository-wide release gate from the
committed tree. That gate belongs to H-1 Stage 3, item 6, and is recorded
separately.
