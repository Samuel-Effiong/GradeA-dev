# H-10 integration: Sections 7 + 8 merged into `beta` — strict gate evidence

This record is kept in the repo on purpose. Evidence held only in a
scratchpad is lost between sessions.

**Owner instruction (2026-09-14):** merge the H-10 dashboard work, then verify
the resulting `beta` tree before closing H-10. The owner chose to merge
**"Section 7 tip + dashboard"**. Section 7 was not yet on `beta`, and the
dashboard branch carried Section 7 only up to `efbeafe`, without its newest
code commit `ac731a9`.

**Result:** the strict gate PASSED on the exact merge commit, `beta` was moved
to it, and H-10 is **CLOSED**. Section 8 status is not changed by this record.

---

## 1. What was merged

| Step | Commits | Result |
|---|---|---|
| 1 | `beta` `084d0e4` + Section 7 gated tip `29f4f59` | `8c438d5`: clean merge |
| 2 | `8c438d5` + Section 8 tip `c6d7cfe` | `2715c64`: one textual conflict |

- **Section 7:** code gated at `2d48d73` (3,959 tests OK); `29f4f59` is
  docs-only on top. It includes the owner's 2026-09-14 upload rules
  (`ac731a9`) and Section 7's own merge of the H-1 user fan-out (`f593be1`).
- **Section 8:** strict-gated at `371268f` (3,994 tests OK); `c6d7cfe` is
  docs-only on top.
- **Kept out, at the owner's request via Section 8:** the Stripe schedule
  commit `d7405c2`.

**Conflict resolution** (`docs/HARDENING_BACKLOG.md` owner table), by row
owner:
- H-12 row from Section 8 (33 files carved out, register);
- H-13 row from Section 7 (DECIDED 2026-09-14).

**Verified before committing the merge:**
- **Section bodies:** each equals its owner's version — H-1 and H-10 from
  `beta`, H-11 and H-13 from `29f4f59`, H-12 from `c6d7cfe`.
- **Audit table:** row 7 = Section 7, row 8 = Section 8.
- **`dashboard/`:** byte-identical to `371268f` (0 diff lines).
- **`.pre-commit-config.yaml`:** identical to `c6d7cfe`.
- **E800 carve-out list:** re-verified with flake8 + eradicate over the
  merged tree. 33 listed = 33 files with hits; none missing, none stale.
  `assignments/tasks.py`, touched by `ac731a9`, has 27 hits and is listed.
  The pre-commit flake8 hook passes on `--all-files`.
- **Conflict markers:** none anywhere.
- **Independent check:** Section 8's session re-checked the merge read-only
  and got the same results.

## 2. Tree under test

| | |
|---|---|
| Commit | `2715c642fc4ba5c58e8898ff808f4ebd672ec28b` (parents `8c438d5`, `c6d7cfe`) |
| **Tree (primary identity)** | **`2a68fe267d666df981f9edfc342111eaddf5801e`**, unchanged after the run |
| Worktree | `../Grade-Automator-Plus-h10-integration`, used only by this gate, `git worktree lock`ed throughout |
| Working tree | 0 porcelain lines before and after; only the gitignored `.env` symlink and `settings_worktree.py` were present |
| Modified during the gate | **0 files** (`find -newer <start marker>`, excluding `.git` and `__pycache__`) |

**About the fingerprint value.** The recorded value
`e3b0c44298fc1c14…` is sha256 of **empty input**. The fingerprint hashes
`git diff HEAD` plus untracked files, so a perfectly clean tree yields exactly
that. As Section 8 pointed out, that value on its own cannot distinguish
"clean" from "the pipeline fed nothing". This gate's cleanliness therefore
rests on the **tree id**, the 0-porcelain counts and the 0-modified-files
check. The gate script now cross-checks the fingerprint against independent
diff-byte and untracked-file counts, and aborts if they disagree.

## 3. Gate results (owner's 2026-09-14 specification)

| Requirement | Result |
|---|---|
| Infrastructure | PostgreSQL 18.6, Redis 8.0.5, Python 3.12.10, Django 5.2.6, redis-py 7.1.0, django-redis 6.0.0 |
| Machine awake | Whole gate under `systemd-inhibit --what=sleep:idle`; `journalctl` shows **0** suspend events after 16:35 |
| Pre-commit | range `084d0e4..2715c64`: **exit 0**, 0 failed hooks |
| System checks | `check`: 0 issues. `check --deploy --fail-level ERROR`: exit 0 with 62 warnings — `drf_spectacular` W001 ×48 and W002 ×8, plus the same 6 environment `security.*` warnings. That is 3 fewer schema warnings than the parent's 65. |
| Migrations | `makemigrations --check`: clean. **264** migrations applied to an empty database: the previous 261, plus `dashboard` 0003 and `students` 0027/0028. `migrate --check` exit 0. |
| Fresh test DB, no `--keepdb` | `test_h10_integration` confirmed absent before the suite. Command: `manage.py test --settings=settings_worktree --noinput -v 2`, with no `--keepdb`, no `--parallel` and no label filter. |
| **Full repository suite** | **Ran 4031 tests in 1822.1s — OK (skipped=14)**, 0 FAIL, 0 ERROR, **exit 0** |
| Unfiltered output | `suite.log`: 40,179 lines, 2,970,627 bytes, sha256 `ee1ca454cacc3903…` |
| Test DB deleted | "Destroying test database" present; `pg_database` rows: **0** |
| Leftover connections | "other sessions using the database" lines: **0**; `pg_stat_activity` rows: **0** |
| Redis | 103 exception lines, all injected by tests: `redis unreachable` 82, `down` 11, `broker unreachable` 5, `transient` 2, `timed out` 1, `connection refused` 1, `result backend unreachable` 1 (`students/tests_task_tracking.py:475`). **0** leftover keys under the suite prefix `gaplus-t493213:*`. |
| Grading pipeline | **252 / 252 ok** |

**Per-module results inside the run** (each test's result line resolved
individually):

| Module | Result |
|---|---|
| `dashboard.tests_dashboard_remediation` (H-10 flatness, parity, tenant) | 47 ok |
| `dashboard.tests_dashboard_audit_fixes` | 23 ok |
| `dashboard.tests_rigor` | 35 ok |
| `dashboard.tests` | 73 ok |
| `dashboard.tests_real_ai_chat` | 2 skipped (opt-in `RUN_REAL_AI`) |
| `AutoGrader.tests_cache_user_fanout` | 29 ok |
| `AutoGrader.tests_cache_dashboard_wide` | 11 ok |
| `AutoGrader.tests_cache_generation_wiring` | 15 ok |
| `students.tests_post_grading_submission_lock` | 29 ok |
| `students.tests_submission_update_freshness` | 4 ok |

## 4. Moving `beta` and the shared checkout

- **Pre-verify.** Before `beta` moved, every one of the 51 merged paths in
  the main checkout was checked:
  - modified/deleted paths were byte-identical to `084d0e4`;
  - added paths were absent;
  - the two paths carrying another session's uncommitted edits matched their
    recorded checksums.
- **Move.** `beta` went from `084d0e4` to `2715c64` by compare-and-swap.
- **File sync.** 48 files were written and indexed, and 3 deletions
  removed. Section 5's two uncommitted files were carried over by a clean
  3-way `git merge-file`, with that session's consent:
  - `docs/CODEBASE_AUDIT_SECTIONS.md`: its Section 5 row sits on top of the
    merged content;
  - `assignments/services.py`: the merge result equalled the new blob
    exactly, because that change was already contained in the merged code.
- **Untouched.** Main-tree status lines outside the merged paths were
  identical before and after. That includes Section 8's uncommitted
  `AutoGrader/settings.py` Beat entry and the staged `.secrets.baseline`.

## 5. H-10 closure

The owner's conditions (`HARDENING_BACKLOG.md` H-10), now all met **on
`beta`** (`2715c64`):

1. **The dashboard fix's own tests pass:** 47 + 23 + 35 + 73 ok inside the
   4,031-test gate.
2. **The H-1 cache suites pass against the resulting code:** fan-out 29,
   dashboard-wide 11, wiring 15, and every other `AutoGrader.tests_cache_*`
   suite. All ok inside the same gate.
3. **Fresh real measurement: query count flat as the data grows.**

| Endpoint | Queries at 2 / 6 / 18 teachers, `1373eae` | Queries at 2 / 6 / 18 teachers, `ec67363` |
|---|---|---|
| super-admin students | 14 / 30 / 78 | **6 / 6 / 6** |
| school-admin teacher_performance | 24 / 60 / 168 | **10 / 10 / 10** |

`dashboard/` on `beta` is byte-identical to the measured and gated Section 8
code.

The owner instructed H-10 to be closed once the post-merge `beta` gate
passed, so **H-10: CLOSED (2026-09-14).**
