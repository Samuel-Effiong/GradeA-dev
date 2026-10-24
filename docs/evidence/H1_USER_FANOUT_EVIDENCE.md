# H-1 Stage 3 item 7 — user-row fan-out: verification evidence

This evidence is kept in the repo on purpose: anything that lives only in a
scratchpad is lost between sessions.

**Scope:** a change to a CustomUser row must reach every *other* user's cached
view that displays or counts that user.

**Owner decision (2026-09-14):** precise fan-out, with no global flush.
The defect was found on `1373eae` by a legacy-disabled probe that returned
16 STALE pairs (`docs/evidence/H1_H2_RELEASE_GATE_EVIDENCE.md` §3).

**Status: FIXED.** Committed as `bfb6d8a` / `f593be1`, and the
committed-tree final gate PASSED (§6). **H-1 overall stays OPEN**: stampede
protection scope and wildcard removal remain.

---

## 1. Change

### `users/signals.py`

**Before the save** (`pre_save`), the receiver reads the row's
**viewer-visible** fields: first, middle and last name, email, `is_active`,
`user_type`, `school` and profile image. It skips this read when
`update_fields` touches none of them.

**After the save**, `clear_user_cache` adds viewer scopes on top of the
existing `usr(self)` / `anyusr` / `global` bump. It adds them only when a
visible field actually changed. The scopes are:

- the user's current school;
- the previous school, on a move;
- `usr` of the teacher of every course the user is enrolled in, plus that
  teacher's school (a single `DISTINCT` query).

**Special cases:**

| Case | Bumped |
|---|---|
| Created with a school | That school (no enrolments exist yet) |
| Deleted | Their school; enrolments and courses reach the teachers through the cascade receivers |

Everything goes out in one pipelined `bump_many`, with duplicates removed.

**New helper:** `invalidate_user_caches(users)`, for writes that bypass
`post_save`.

### `users/admin.py`

The bulk *activate* and *deactivate* actions used `QuerySet.update()`. That
fires no signal, so **neither** mechanism ever saw those changes, even though
`is_active` is shown on other users' views. Both actions now capture the rows
first and call the helper: one teacher query and one round trip for any
number of users.

### `AutoGrader/tests_cache_dashboard_wide.py`

`test_a_new_user_does_not_touch_the_school_table_counter` asserted that a new
teacher in school A left `sch:A` unchanged. **That test encoded the defect.**
It now requires `sch:A` to move, while `anysch` and `sch:B` stay unchanged.

### `AutoGrader/tests_cache_generation_wiring.py`

The regression run found a **second test encoding the same defect**.
`test_saving_a_user_bumps_only_that_user` renamed a teacher and asserted the
school generation must not move. The school admin's teacher list shows that
name and is keyed on the school, so the assertion is wrong. It now requires
the teacher and school to move, while the student and course stay unchanged.

Two committed tests had asserted the stale behaviour as correct. That is why
the Stage 2 proofs never caught this class of bug.

### Checked, and needs no change

Paths that write the lockout counters use `QuerySet.update()`, but those
counters are not viewer-visible. Logins do not save the row, since nothing
updates `last_login`.

## 2. Tests — `AutoGrader/tests_cache_user_fanout.py`

40 tests including the dashboard-wide suite. They run on **real Redis** and
**real Postgres**. The **legacy wildcards are disabled** in all four signal
modules, and a guard test proves the patch took.

| Class | What it proves |
|---|---|
| `UserRowFreshnessTests` | All 16 probe-STALE pairs are now FRESH, compared at response level against an uncached read. Each check also asserts the payload really changed, so none can pass trivially. Also covers the full-save path and single-save creation. |
| `UserRowPrecisionTests` | **Isolation.** What must NOT move: unrelated teachers in the same school, other schools, the admin's own `usr`, `anysch`, `crs`. Other-tenant dashboards are byte-identical after a burst of school-A user changes. Invisible field changes (`bio`, lockout counters, password) and a `Settings` save do not fan out. |
| `UserRowCostTests` | Fan-out queries do not change between 1 and 31 courses. A visible change costs exactly +2 queries over an invisible one. One `bump_many` per save. |
| `AdminBulkActionTests` | Bulk deactivation reaches the teacher's roster. The bulk action is precise and issues exactly one course query. |
| `UserRowFailureTests` | A Redis outage never fails the user save. |
| `UserRowConcurrencyTests` | 8 barrier-synchronised concurrent student renames each bump the shared teacher (no lost INCR), and no other school moves. Every thread closes its own DB connection (the H-2 rule). |

## 3. Mutation testing

Each mutant was applied to a checksum-backed copy, AST-checked and proven
present. The suite was run, then the file was restored and md5-verified.
`git checkout` was never used.

| Mutant | Verdict | Tests that failed |
|---|---|---|
| F1 current/previous school scopes dropped | KILLED | 11 |
| F2 previous school forgotten on a move | KILLED | 5 |
| F3 teacher `usr` fan-out dropped | KILLED | 15 |
| F4 teacher's school dropped | KILLED | 4 |
| F5 pre_delete teacher capture dropped | **SURVIVED → redundant, removed** | — |
| F5b delete does not move the user's own school | KILLED | 1 |
| F6 created user gets no school bump | **SURVIVED → test gap closed → KILLED** | 2 |
| F7 visibility filter disabled | KILLED | 1 |
| F8 unchanged-value check removed | KILLED | 1 |
| F9 fan-out never wired into `clear_user_cache` | KILLED | 26 |
| F10a admin activate not invalidated | KILLED | 1 |
| F10b admin deactivate not invalidated | KILLED | 1 |
| F11 pre_save reads the row for invisible updates | KILLED | 1 |
| F12 per-course teacher lookup (N queries) | KILLED | 3 |

**What the survivors taught:**

- **F5 — redundancy, not a gap.** A deleted student's enrolments are
  CASCADE-deleted first. The `StudentCourse` receiver then bumps each
  course's teacher and school. The `pre_delete` lookup was deleted rather
  than kept as unproven code.
- **A real gap found while analysing F5.** A teacher with **no courses** has
  nothing to cascade, so their school's teacher list stayed stale. The delete
  now bumps the user's own school (F5b).
- **F6 — a test gap.** `make_user` sets the school in a *second* save, so the
  `created=True` branch was never exercised. Added single-save creation tests.

## 4. Before / after measurement

Measured on real Postgres and real Redis with the same test on both trees.
Each cell is the median of 15 saves. The student is enrolled in K courses,
each with a different teacher.

| Tree | K | Save | Queries | Scopes bumped | Redis round trips | p50 ms |
|---|---|---|---|---|---|---|
| `1373eae` | 1 / 10 / 50 | visible `first_name` | 1 / 1 / 1 | 3 / 3 / 3 | 1 | 3.75 / 3.53 / 3.79 |
| fix | 1 / 10 / 50 | visible `first_name` | **3 / 3 / 3** | 5 / 18 / 58 | **1** | 4.35 / 4.45 / 8.05 |
| `1373eae` | 1 / 10 / 50 | invisible `bio` | 1 / 1 / 1 | 3 | 1 | 4.02 / 4.35 / 3.93 |
| fix | 1 / 10 / 50 | invisible `bio` | **1 / 1 / 1** | **3** | 1 | 2.44 / 2.39 / 3.17 |

How to read this:

- **Query count is flat** in the number of courses: +2 for a visible change
  (the pre-save read and one `DISTINCT` teacher lookup), +0 for an invisible
  one.
- **Scopes bumped grow with the number of teachers affected.** That is the
  owner-approved cost model: "O(affected existing users) for legitimate
  per-user invalidation", with zero SCANs and one round trip. At 50 teachers
  the save's p50 rises by ~4ms.
- **Frequency is low.** The fan-out runs only on profile, membership or
  activation changes, never on logins or lockout updates.
- **Staleness: 16 STALE → 0** under the legacy-disabled probe matrix.

## 5. Regression

### First run

`AutoGrader classrooms users students assignments dashboard` on the fix
worktree ran **1,832 tests with 1 failure** (skipped=12) in 1018s.

- The failure was `test_saving_a_user_bumps_only_that_user`, the second
  defect-encoding assertion described in §1. It was corrected.
- Teardown was clean: the test database was destroyed, with 0
  "other sessions" lines.

### Re-run after the correction

The three suites touched by the correction ran together:
`tests_cache_generation_wiring`, `tests_cache_user_fanout` and
`tests_cache_dashboard_wide`. **55 tests OK.** The full repository suite then
ran inside the final gate (§6).

## 6. Final release gate — committed tree `f593be1`

Run to the owner's 2026-09-14 specification, in the dedicated worktree
`../Grade-Automator-Plus-h1-user-fanout`. No other session used it, and it was
`git worktree lock`ed for the whole run. The whole gate ran under
`systemd-inhibit --what=sleep:idle`, and `journalctl` shows **0** suspend
events after 14:35.

| # | Requirement | Result |
|---|---|---|
| 1 | Exact commit / tree / fingerprint | commit `f593be1bd3a06e165670724b23c32b9c139a6247` (= `beta`; the fix is `bfb6d8a`, then a one-line doc correction), tree `77a0094066daac26a78cbdf2229c06b099546c42`. NUL-safe sha256 fingerprint `e3b0c442…b855` (the empty input: no diff, no untracked files) **before and after**. 0 porcelain lines before and after. |
| 2 | No other session modified the code under test | HEAD and tree unchanged after the run; `find -newer <start marker>` (excluding `.git` and `__pycache__`) returned **0 files**; worktree locked throughout |
| 3 | Infrastructure | PostgreSQL 18.6, Redis 8.0.5, Python 3.12.10, Django 5.2.6, redis-py 7.1.0, django-redis 6.0.0 |
| 4 | Pre-commit | range `1373eae..f593be1`: **exit 0**, 0 failed hooks |
| 5 | System checks | `check`: 0 issues. `check --deploy --fail-level ERROR`: exit 0, with the same 65 warnings the parent already had (environment and schema, see the `1373eae` gate §2a) |
| 6 | Migrations | `makemigrations --check`: no changes. **261** migrations applied to an empty database, and `migrate --check` exit 0 |
| 7 | Fresh test DB, **no `--keepdb`** | `test_h1_user_fanout` confirmed absent before the suite. Command: `manage.py test --settings=settings_worktree --noinput -v 2` (no `--keepdb`, no `--parallel`, no label filter) |
| 8 | Full repository suite | **Ran 3888 tests in 1291.6s — OK (skipped=12)**, 0 FAIL, 0 ERROR, **exit 0**. That is 3,859 at `1373eae` plus the 29 new fan-out tests. |
| 9 | Complete, unfiltered output | `suite.log`: 39,633 lines, 2,910,690 bytes, sha256 `8c12365439c12b2d…` |
| 10 | Test DB deleted afterwards | "Destroying test database" present; `pg_database` rows for it: **0** |
| 11 | No leftover PostgreSQL connections | "other sessions using the database" lines: **0**; `pg_stat_activity` rows: **0** |
| 12 | Redis | 101 exception lines, all injected by tests: 82 `redis unreachable` (77 from the classrooms resilience suite + 5 from `UserRowFailureTests`), 11 `down`, 4 `broker unreachable`, 2 `transient`, 1 `timed out`, 1 `connection refused`. **0** leftover keys under the suite's prefix `gaplus-t412782:*` |
| 13 | Grading pipeline | **212 / 212 ok** |

Real-AI tests stayed skipped: `RUN_REAL_AI` was unset, so no billed calls were
made.

**Conclusion.** Stage 3 item 7 is fixed and passes the committed-tree gate.
This document's own addition, and the status lines updated alongside it, went
in afterwards as a **docs-only** commit; its diff touches nothing outside
`docs/`.
