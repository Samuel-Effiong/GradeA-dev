# H-237: at_risk counts only submissions to published, due work — mutation chain

Branch `task/h237-at-risk-due-work-only`, frozen tip **`4b4cde5c`** (the H-237
code at `7a01d13f`, rebased onto beta `eca3d034`, plus the rebase/regression
evidence commit — inert to every test here). Run by `fixes-coordinator`
(`grade-automator-plus-5e`), 2026-10-15, per the Senior Manager's instruction
to match H-233/H-234/H-235's precedent before landing review. Companion doc:
`REBASE_AND_REGRESSION.md` in this same directory covers the rebase and the
broader app regression; this doc covers Gate 2 only.

## The rule and its 8 implementation sites

One shared definition (`assignments/models.py`: `is_expected`,
`is_expected_by_now`, `expected_q`) says what "published and due (or
undated)" means, read independently by 8 call sites across 4 files:

| Site | File | Mechanism |
|---|---|---|
| my-students | `students/serializers.py` `_risk_result` | Python filter over prefetched submissions, via `_is_expected_by_now` |
| all-students | `dashboard/views.py` (`all_students` action) | `risk_submitted_val` annotation, `expected_q(...)` |
| per-course students | `dashboard/views.py` (`students` action) | `risk_submitted_val` annotation, `expected_q(...)` |
| session overview | `dashboard/views.py` (`overview` action) | `risk_submitted_val` annotation, `expected_q(...)` |
| school weekly at-risk set | `dashboard/services.py` `SchoolAdminWeeklySummaryService` | `expected_q()` query + `due_assignment_ids` set membership |
| teacher AI teaching data | `dashboard/services.py` `TeacherAIContextService` | `is_expected(...)` + `due_ids` set membership |

Because every site is a SEPARATE piece of code reading the one rule (not one
function called everywhere), each needed its own mutant: a shared-helper
mutation alone wouldn't prove a given call site actually applies the filter
it receives.

## Method

A self-contained driver (`GAP-5e-runs/h237/mutants.py`, not committed —
project tooling convention keeps run scripts outside the repo, same as the
sibling rows' `GAP-58-runs`): for each mutant, apply ONE textual
substitution (a single, pre-verified-unique anchor per file), run the
targeted test module(s), then restore the file from `git show 4b4cde5c:<path>`
and verify its sha256 against the pre-mutation hash before moving on — a
restore is never assumed, only a verified match counts (per the project's
standing "restore mutants by checksum, not by trusting the script" rule).
Baseline (all 8 mutants' combined test labels, unmutated) confirmed green
before any mutant was applied.

## Mutants and results

| Mutant | File | What it does | Failing tests | Verdict |
|---|---|---|---|---|
| M1 | `assignments/models.py` | `is_expected` drops the due-date gate (keeps only the PUBLISHED check) | `test_m2_my_students_early_submissions_cannot_hide_missed_work` | KILLED |
| M2 | `assignments/models.py` | `expected_q` drops the due-date half (`published_q` alone) | `test_a2_all_students_early_submissions_cannot_hide_missed_work`, `test_names_and_the_built_payload_match`, `test_same_students_scores_and_order_as_the_original` | KILLED |
| M3 | `dashboard/services.py` | `TeacherAIContextService`: submitted count drops the `& due_ids` filter | `test_i1_ai_teaching_data_the_users_example_is_at_risk` | KILLED |
| M4 | `dashboard/services.py` | `SchoolAdminWeeklySummaryService`: submitted count drops the `due_assignment_ids` filter | `test_w1_weekly_school_set_the_users_example_is_at_risk`, `test_names_and_the_built_payload_match`, `test_same_students_scores_and_order_as_the_original` | KILLED |
| M5 | `dashboard/views.py` | overview action: risk evaluator reads `submitted_count_val` (unfiltered) instead of `risk_submitted_val` | `test_o1_overview_the_users_example_is_listed_at_risk` | KILLED |
| M6 | `dashboard/views.py` | per-course students action: same swap | `test_c1_course_students_the_users_example_is_at_risk` | KILLED |
| M7 | `dashboard/views.py` | all-students action: same swap | `test_a1`, `test_a2`, `test_a3`, `test_a4` (all 4 all-students at-risk tests) | KILLED |
| M8 | `students/serializers.py` | my-students `_risk_result`: submitted count drops the `_is_expected_by_now` filter entirely (`len(submissions)`) | `test_m1`, `test_m2`, `test_m3`, `test_m4` (all 4 my-students at-risk tests) | KILLED |

All 8/8 killed. Every restore verified: post-restore sha256 of
`assignments/models.py`, `dashboard/services.py`, `dashboard/views.py`, and
`students/serializers.py` matched the pre-mutation hash (taken fresh from
each commit blob) in every case; `git status --short` was empty after the
run.

## One finding worth recording, not a defect

M1 is visibly a *weaker* mutant than M8 even though both touch the
"my-students" path: M1 only breaks `test_m2` (the "critical" fixture),
not `test_m1`/`test_m3`/`test_m4`. Reason: `_risk_result`'s
`expected_count` is computed with the SAME `_is_expected_by_now` call as
`submitted_count`, so removing the due-date gate inflates both figures
roughly in step for the "user's example" fixture (2 due + 1 early
submission among 3 published assignments) and the ratio still lands on
the at-risk side by coincidence; the "critical" fixture's numbers (3 due +
2 early submissions) don't survive that coincidence. This is exactly the
kind of gap mutation testing exists to surface — a single mutant can look
"killed enough" while actually only catching one of several cases — which
is why M8 (mutating the call site directly, not the shared helper) is the
test that proves `students/serializers.py` really applies the filter on
its own, independent of what `expected_count` happens to do. Both are kept
in the table above as the honest record of what each mutant actually
caught.

## Housekeeping

- Test databases from the mutation runs were ephemeral (no `--keepdb`);
  confirmed none left behind (`SELECT datname ... LIKE '%h237%'` on
  Postgres returns empty after the run) and no stray `manage.py test`
  processes.
- Worktree clean, no uncommitted changes, before and after the chain.
