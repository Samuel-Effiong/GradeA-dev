# H-1 / H-2 release gate — committed tree `1373eae`

This record is kept in the repo on purpose: evidence left only in a scratchpad
does not survive a session restart.

**Status: the gate PASSED for `1373eae`. This is NOT release approval, and it
does not close H-1 Stage 3 item 6.** After the gate, a legacy-disabled probe
found a Stage 3 blocker (§3): user-row changes don't reach other users' cached
views. Fixing it means a new commit, and that commit needs a new gate.

---

## 1. Tree under test

| | |
|---|---|
| Commit | `1373eaeac02d14d6ab79d532be56db1eb7fc4585` on `beta` (parent `06516cf`) |
| Tree | `ba7011a4031e2a44fcf0f80198296d486bc87354` |
| Where | dedicated worktree `../Grade-Automator-Plus-h1-h2-release-gate` (branch `task/h1-h2-release-gate`). No other session used it. |
| Working tree | clean: 0 porcelain lines before and after. Only the gitignored `.env` symlink and `settings_worktree.py` were present. |
| Fingerprint (NUL-safe, sha256) | before `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`; after: identical |

The fingerprint is `sha256("")`, meaning there was no diff and no untracked
file, so the tested tree is exactly the commit. HEAD and the tree hash were
unchanged afterwards.

**What the commit contains.** 41 files:
- H-1 Stage 2
- H-9 (test-cache isolation)
- H-2 (per-thread connection close)
- the Section 3 classrooms work they are built on
- docs

Every changed line was attributed to this session's own edits. Excluded
because other sessions own them:
- `ai_processor/`
- `assignments/services.py`
- `assignments/tests_real_extraction.py`
- `docs/CODEBASE_AUDIT_SECTIONS.md`
- `.secrets.baseline`
- phase-2 docs
- the Stripe-price schedule hunks in `AutoGrader/settings.py`

The commit was built in its own worktree. `beta` was moved by compare-and-swap.

## 2. Gate results (the owner's 10-state standard)

| # | State | Result |
|---|---|---|
| 1 | Tree identity | above |
| 2 | Infrastructure | PostgreSQL 18.6, Redis 8.0.5 (local `noeviction`; production is `volatile-lru`), Python 3.12.10, Django 5.2.6, redis-py 7.1.0, django-redis 6.0.0, psycopg2 2.9.10 |
| 3 | Pre-commit | `pre-commit run --from-ref 06516cf --to-ref HEAD`: **exit 0**, 0 failed hooks (black, mypy, flake8, isort, bandit, detect-secrets, …) |
| 4 | System checks | `check`: **0 issues, exit 0**. `check --deploy --fail-level ERROR`: **exit 0** with 65 warnings, see §2a |
| 5 | Migrations | `makemigrations --check`: no changes. **261 migrations applied to an empty database**, exit 0. `migrate --check`: exit 0. That database was dropped afterwards. |
| 6 | Full repository suite | `manage.py test` (all apps, `-v 2`): **Ran 3859 tests in 1650.3s — OK (skipped=12)**, 0 FAIL, 0 ERROR, **exit 0** |
| 7 | Teardown / connections | test DB destroyed; 0 "other sessions" lines; **0 leftover test DBs, 0 leftover connections** |
| 8 | Redis | 96 exception lines, **all injected by tests** (§2b); **0 leftover keys** under the suite's per-process prefix `gaplus-t191419:*` |
| 9 | Grading pipeline | 212 grading-pipeline tests: **212 ok**, 0 skipped, 0 failed (§2c) |
| 10 | Exit + reproducibility | suite exit 0; fingerprint, HEAD and tree unchanged after the run |

Real-AI tests stayed skipped: `RUN_REAL_AI` was explicitly unset, so no
billed calls were made.

### 2a. The 65 deploy warnings existed before this commit

The same command on the parent commit `06516cf`, in a throwaway detached
worktree, returns the identical set:

| Warnings | Source |
|---|---|
| 51 × W001, 8 × W002 | `drf_spectacular` schema |
| 1 each of W004, W008, W009, W012, W016, W018 | `security.*`, from the local development `.env` (DEBUG, HSTS, SSL redirect, secure cookies, dev SECRET_KEY) |

So they come from the environment and existing code, not from this commit.
They are not production settings.

### 2b. Redis exception lines, each traced to its source

| Count | Message | Injected by |
|---|---|---|
| 77 | `ConnectionError: redis unreachable` | `classrooms/tests_concurrency_and_resilience.py:286,382` |
| 11 | `ConnectionError: down` | `AutoGrader/tests_cache_generation.py:197` |
| 2 | `ConnectionError: transient` | `AutoGrader/tests_cache_generation.py:235` |
| 4 | `ConnectionError: broker unreachable` | `assignments/tests_prerender.py:258,296` |
| 1 | `TimeoutError: timed out` | `assignments/tests_prerender.py:274` |
| 1 | `ConnectionError: connection refused` | `AutoGrader/tests_dispatch.py:27` |

### 2c. Grading-pipeline accounting

The modules counted are `students.tests_grading*`,
`ai_processor.tests_grading*`, `ai_processor.tests_objective_pipeline`,
`ai_processor.tests_second_opinion_pipeline`, and the students grading suites.
A naive single-line parse showed 79 "not ok". Those tests log between the test
name and the result. Resolving each test's result line properly gives 212 ok.
The last four, the `tests_grading_benchmark.ReplayRunTest` replays, print
~775 lines each before `ok`.

### 2d. Anomaly: the machine suspended mid-suite

The suite started at 19:19:41. `systemd` suspended the laptop at 19:31:11
(journal: `PM: suspend entry (s2idle)`) and it resumed at 09:43:38 the next
day. The suite then finished at 10:00:13. Every test passed, and teardown and
connections were clean after the resume. It is recorded rather than hidden.
The next gate (required anyway, §3) should run with suspend inhibited, e.g.
`systemd-inhibit --what=sleep`.

## 3. Stage 3 blocker found after the gate: user-row changes

**The claim tested.** A change to a CustomUser row (name, email,
`is_active`, `school`) should reach every *other* user's cached view that
displays or counts that user, with the legacy wildcards disabled.

**Method.** The probe was `AutoGrader/tests_probe_user_fanout.py`, in a
detached worktree at `1373eae`:
`../Grade-Automator-Plus-h1-user-probe`, not committed. It used real
PostgreSQL and Redis, with `delete_cache_patterns` patched out in all four
signal modules and a guard test proving the patch took. For every mutation
and endpoint it compares three reads:
- the cached read before the mutation
- the cached read after it
- an uncached read after it

Endpoints whose uncached reads differ with no mutation would be classified
VOLATILE. None were.

**Result: 16 STALE cells.** `-` means the payload doesn't reflect that
mutation.

| Endpoint | M1 teacher rename | M2 student rename | M3 teacher joins school | M4 teacher moves school | M5 student deactivated | M6 student email |
|---|---|---|---|---|---|---|
| 23 school summary | - | - | **STALE** | **STALE** | **STALE** | - |
| 25 school-admin students | - | - | - | **STALE** | - | - |
| 30 teacher_performance | **STALE** | - | **STALE** | **STALE** | - | - |
| 31 teacher_detail | fresh | - | - | fresh | - | - |
| 33 department_overview | - | - | - | **STALE** | - | - |
| 26 / 27 / 28 teacher dashboards | - | - | - | - | - | - |
| 29 teacher students | - | **STALE** | - | - | - | - |
| teacher course-list (mixin) | - | **STALE** | - | - | **STALE** | **STALE** |
| teacher course-detail (mixin) | - | **STALE** | - | - | **STALE** | **STALE** |
| teacher student-course-list (mixin) | fresh | - | - | - | - | - |
| teacher submission-list (mixin) | - | **STALE** | - | - | - | - |
| 11 student my_courses | - | fresh | - | - | fresh | fresh |
| student course-list / submission-list | - | fresh | - | - | fresh | fresh |

**Cause.** `users.signals.clear_user_cache` bumps only `usr(self)`, `anyusr`
and `global`. Views that show a user to *someone else* are keyed on:
- the **school** (23, 25, 30, 33), or
- the **viewing teacher** (29, course and submission lists).

Neither of those generations moves when that user's own row changes.

**Production impact today: none.** The legacy receiver still sweeps `*user*`,
`*school*` and `*course*` on every CustomUser save. That is the same
dual-running masking recorded in the design doc.

**Consequences.**
- It **blocks Stage 3** (wildcard removal).
- Families 23, 25, 29, 30 and 33, plus the course/submission mixin
  families, were recorded DONE on freshness proofs. None of those proofs
  exercised a *user-row* mutation.

The fix must go through the full 13-point standard and a new committed-tree
gate.
