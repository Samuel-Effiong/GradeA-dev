# Section 8 (dashboard) remediation: verification evidence

Status: **GAPS FOUND / BLOCKERS FIXED / REMEDIATION IN PROGRESS.** Not complete.
Two things are still open:

- item 9, E800, sequenced behind the Section 7 session's `.pre-commit-config.yaml` change;
- the repository-wide gate on the final reconciled tree.

The sections below record what has been verified so far. The measurement scripts ran in throwaway copies; the numbers are recorded here because the scratchpad does not survive sessions.

**Tree.** Branch `task/dashboard-audit-reconciled` is based on `1373eae`, the H-1 cache-generation commit. The Section 8 changes are applied on top and uncommitted.
Dashboard Python fingerprint at the time of writing: `sha256(sorted sha256 of dashboard/**/*.py) = 559d86fb100fa430…`.

## 1. Reconciliation with H-1

- **Original merge check.** The Section 8 work was developed on `06516cf`. It was carried onto `1373eae` as a binary patch with `git apply --3way`: every file applied cleanly and no conflict markers appeared.
- **`dashboard/views.py`.** It keeps all 19 H-1 `versioned_key` hunks and H-1's concurrency no-cache change. The other 16 files are byte-identical to the pre-merge backup.
- **Earlier snapshot check.** A `git merge-file` three-way merge was run against H-1's uncommitted snapshot, which is byte-identical to `1373eae:dashboard/views.py`. It reported 0 conflicts.
- **What the rewritten views read.** The rewritten families read the same tables as before. Families 30 and 31 return an identical payload key set: an AST diff of the old `compute_teacher_performance_stats` against the new `TeacherPerformanceStatsService._payload` found no difference.
- **Coordinated with the H-1 session.** It confirmed the Section 8 rewrite as the candidate fix for H-10, so it will not write a competing one. It also owns a known cache-wiring gap: family 30 is keyed on `sch`, but a `CustomUser` save does not bump `sch`. The fix belongs in `users/signals.py`. H-1's six cache suites do not yet cover that case.

## 2. Before and after, at three dataset sizes

Measured through the real endpoints with the provider mocked. Every figure is the number of queries per request; the two AI-chat rows also give the context size.
**Before** is the original `views.py`, `services.py` and `tasks.py`. **After** is this branch.
Sizes are per school: teachers × courses × assignments × students. Super-admin figures are platform-wide and cumulative.

| Endpoint | small 2×1×3×5 | medium 5×2×8×15 | large 10×3×15×25 | after, all sizes |
|---|---|---|---|---|
| super-admin teachers | 8 | 18 | 38 | **8** |
| super-admin schools | 4 | 4 | 4 | **8** (before was constant but wrong, see §3) |
| super-admin students | 10 | 30 | 90 | **6** |
| school teacher-performance | 27 | 54 | 99 | **13** |
| school students | 9 | 25 | 65 | **5** |
| school AI chat | 32 q / 962 ch | 48 q / 970 ch | 88 q / 972 ch | **36 q**, 1,347 / 2,082 / 3,301 ch |
| teacher AI chat | 65 q / 7,762 ch | 154 q / 68,227 ch | 307 q / 269,758 ch | **17 q**, 3,125 / 12,959 / 27,918 ch |
| teacher students (one course) | 6 | 6 | 6 | **8**, now one page of 20 |
| weekly digest teacher activity | 20 | 44 | 84 | **7** |

- **School AI chat context.** Before, it was ~970 characters at every size because the teachers section was always `{}`. After, it grows only with the teachers now actually listed; the cap is 100, with a stated total.
- **Teacher AI chat context.** It is about 10× smaller at the large size, and `limits` reports that nothing was left out. The large teacher (3 courses, 45 assignments, 75 enrolments) is under every cap.

## 3. Correctness reproduced before fixing

All of these came from probes against the original code; each has a regression test.

- **Super-admin teacher counts.** A teacher with 1 course was reported as having **18**.
- **Super-admin averages.** A 50.0 average was reported as **83.33** for both teachers and schools; the join weighted it by assignment and submission count.
- **Super-admin completion rate.** A course with 10 assignments and no students, plus a course with 30 students and no assignments, "expected" 300 submissions instead of 0. The rate came out as 0.27 instead of 50.0.
- **School AI chat.** The teachers section was always `{}`, because `self.teachers` does not exist on that view and the error was swallowed.
- **At-risk alert.** With the broker down, run 1 queued 0 emails; after recovery, run 2 also queued 0. The alert was lost.
- **Flagged-for-review threshold.** It used a bare 70, while the canonical `AI_CONFIDENCE_THRESHOLD` is 80. Product decision: use 80.
- **Teacher `students` endpoint.** It accepted `page`/`page_size` but returned every row.

## 4. The regression tests fail against the original code

`dashboard/tests_dashboard_remediation.py` was run in a copy with the ORIGINAL `views.py` and `tasks.py`.

- **31 of 38 selected tests fail.** That is 28 of the first 35, plus the 3 later view-level cost tests. Their failures reproduce the defects:
  - 18 vs 1 course, 83.33 vs 50.0, 0.27 vs 50.0
  - teacher list 24 → 159 queries; super-admin students 10 → 50; school students 9 → 49
  - school chat 29 → 53; teacher chat 51 → 234
  - `alert_pending` absent, and the alert not redelivered
  - teachers section `{}`; no error logged
  - transaction held across the provider call on all three chats
  - list response, not the pagination envelope
  - 0 vs 2 flagged
  - `compute_teacher_performance_stats` still exists
- **The 7 that pass on the original code are deliberate guards,** not defect reproductions:
  - exact school counts
  - school-list query constancy (it was already a constant 4, but wrong)
  - teacher-list tenant boundary
  - school completion scope
  - no duplicate alert after delivery
  - recovery discharges a pending alert
  - a failed provider call persists nothing
- **Original `services.py` too.** The digest teacher-activity cost test fails with `42 != 21`, which is 7 queries per added teacher.
- **Earlier blocker tests.** `dashboard/tests_dashboard_audit_fixes.py` (23 tests) has 17 failing against the original code.

## 5. Real provider calls

`dashboard/tests_real_ai_chat.py`, run with `RUN_REAL_AI=1`. Both calls go through the real billing gate (an active subscription or license allocation plus a funded wallet), and both assert a `CreditUsageLog` row was written.

- **Teacher chat.** Setup: 3 courses × 8 assignments × 12 students, with an invented struggling student and a distinctive hard assignment planted.
  - Reply: *"…the student with the lowest average score is Quintessa Varga, with an average of 11.0 … The assignment with the lowest average score is Chloroplast Pigment Chromatography, with an average of 31.0."*
  - Context was 15,119 characters and reported "Nothing was left out".
- **School-admin chat.** Setup: invented teachers with 4, 1 and 1 courses, plus another school's teacher with 9 courses.
  - Reply: *"Ignatius Thornbury teaches the most courses, with exactly 4 courses."*
  - The other school's teacher appears in neither the context nor the reply.

## 6. Test runs

- **Pre-reconciliation tree.** 327 tests OK, 2 skipped (the opt-in real-AI tests): `dashboard`, `classrooms`, `billing.tests.test_custom_ai_prompt_beta`, `billing.tests.test_execute_graded_task`.
- **Reconciled tree.** H-1's six cache suites plus the same regression: see §7.
- **Migrations.** `makemigrations --check` reports no changes. `dashboard/0003_studentriskalertstate_alert_pending` is a single `AddField` with a default.

## 7. Reconciled-tree gate (`1373eae` + Section 8 patch; `dashboard/views.py` md5 `e526652cf247…`)

| Check | Result |
|---|---|
| `makemigrations --check` | No changes detected |
| H-1 cache suites: `tests_cache_dashboard_wide`, `_freshness`, `_2329`, `tests_cache_superadmin_1522`, `tests_cache_invalidation_coverage`, `tests_cache_collateral_damage`. Legacy invalidation is disabled inside them, so a missing scope bump fails. Real Redis and Postgres. | **80 tests OK** |
| `dashboard`, `classrooms`, `billing.tests.test_custom_ai_prompt_beta`, `billing.tests.test_execute_graded_task` | **485 tests OK**, 2 skipped (opt-in real AI) |

These suites cannot show the family-30 teacher rename or join case; that belongs to the H-1 session, as described above.
| Interim full repository suite (`python manage.py test --settings=settings_worktree --parallel 1`) | **3931 tests OK**, 14 skipped, exit 0. The teardown "other sessions" line was not captured. |

The full-suite run above is interim. It ran before item 9 (E800) and used `--keepdb`. A `--keepdb` run never drops the test database, so it cannot detect a leaked-connection regression (H-2): the "N other sessions using the database" failure only fires on that DROP.

The **repository-wide gate** on the final tree, once item 9 lands, will therefore:

- use a fresh test database, **without** `--keepdb`;
- keep the full output;
- run under `systemd-inhibit --what=sleep`;
- afterwards confirm there is no `test_dashboard_audit_reconciled` row in `pg_database` and 0 connections to it in `pg_stat_activity`.

## 8. Item 9: commented-out code (E800), dashboard part

**Division of work.** The Section 7 session owns the E800 switch-on in `.pre-commit-config.yaml`: commit `54d3305`, which exempts 39 files. Its H-12 backlog entry says that list may only shrink. Section 8 cleans its own files and then removes their entries.

**Removed.** 84 E800 hits across `dashboard/views.py` (79), `dashboard/serializers.py` (4) and `dashboard/tests_rigor.py` (1), plus the unflagged lines of the same dead blocks:

- **Imports and names:** three commented-out imports, and a stale inline `# SchoolAdminTeacherPerformanceSerializer,` on an import line.
- **Decorators:** every commented-out `@method_decorator(cache_page…/vary_on_headers…)` stack.
- **Dead values:** two dead dict keys and a dead `description=`.
- **Class attributes:** three `# http_method_names` lines.
- **Dead statements:** `total_students`, `active_students` and `student_summary_async`.
- **Whole methods:** the entire commented-out school-admin `teachers` method (92 lines). `teacher_performance` superseded it.
- **Serializer leftovers:** an old `Meta` block in a plain `Serializer`.
- **Unfinished feature:** the stubbed hardest/easiest-questions block. Its FIXME is kept as a one-line plain-English note.
- **Reworded, not removed:** the arithmetic comment in `tests_rigor.py`. It was a false positive that explains a test value.

**Proof that no code changed.** Each file was parsed before and after and its AST compared, with import statements normalised because isort reflowed blank lines once a comment was gone. All three files are **identical**. The pre-cleanup snapshot was confirmed by checksum.

**Result.** `flake8 --select=E800 dashboard/` finds **0** (migrations excluded, as in the hook). Pre-commit passes. The diff is 187 lines removed and 4 added.

**Regression after the cleanup.** The full `dashboard` test suite passes: **218 tests OK**, 2 skipped (opt-in real AI).

**Carve-out list: done.** Section 7's `54d3305`, which switches E800 on, was merged into this branch. All six dashboard entries were then removed from `--per-file-ignores`:

- `views.py`, `serializers.py`, `tests_rigor.py`: now clean;
- `urls.py`: already clean;
- `at_risk_improvements.py`, `AT_RISK_IMPLEMENTATION_GUIDE.py`: deleted in item 8.

The remaining list was checked to equal **exactly** the set of files that still have E800 hits, with no stale entries and no missing ones. `pre-commit run flake8 --all-files` passes with it.

**Item 9 overall: open.** The owner decided on 2026-09-14 that item 9 covers the whole repository. 33 files and 351 hits remain. Each is tracked with its hit count, what is commented out, reason, owning section and staged plan in the `docs/HARDENING_BACKLOG.md` H-12 register. Entries are removed as each area is cleaned, and no directory-wide carve-outs are allowed. `migrations/` is excluded from the whole flake8 hook because it is generated code; that exclusion is documented in the register.

## Known consequences, accepted or handed off

- **AI chat credits.** Credits are consumed by the billing layer during the provider call, which is no longer inside the chat transaction. If writing the chat turn afterwards failed, the credits would stay spent with no stored reply. The old design held a DB transaction open across up to three provider round trips.
- **Partial alert queue failure.** The whole alert is retried, so an admin whose copy did queue may receive it twice. This is deliberate: a duplicate is recoverable, a lost alert is not.
- **Teacher `students` response shape.** It is now the `StandardPageNumberPagination` envelope, with the OpenAPI schema `PaginatedTeacherStudentAnalytics`. Frontend consumers must read `results`.
- **Family-30 cache staleness on teacher rename or join.** An H-1 wiring gap, owned by that session.
