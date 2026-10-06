# BE-I-04 slice C: the label written with the grade

Author: the Next-stage Builder, 2026-10-06. Branch `task/epic-i-be-i-04-c`, off slice B's
re-frozen tip 6ca94c09; a base update onto B's merge follows. Phase 2 line only. Verifier: the
Next Stage Checker, independent. Design: `~/Documents/Projects/GAP-planning/BE-I-04-design-note.md`
with the SM's rulings (plan, Part 10).

## Order of work (SM ruling: red tests are their own commit first)

1. **This commit: the tests only.** Four new test modules, 90 tests, and this file. No
   production code is changed by it.
2. Before the code commit, a list to the SM mapping these tests to its rulings.
3. Then the code. The red run of these tests against the code as at this commit is the first
   step of the code's gate. No run is asked for before the Checker's verdict on slice B.

## The four modules

| Module | Tests | What it holds |
|---|---|---|
| `ai_processor/tests_grading_run_label.py` | 37 | The rules that turn what a run kept into the six label fields and the three audit lists: majority per answer, the tie rule, the backup flag's four answers, kept replies only, the second opinion apart |
| `ai_processor/tests_grading_run_pipeline.py` | 20 | That the grading service keeps the right things on every route (short paper, long paper in parts, wholly reused, wholly fixed-rule, mixed, a rejected reply, a failed attempt, a second opinion); that the grading code reads the run's reading; a static check that no grade-shaping setting is read live |
| `students/tests_grading_label_written.py` | 29 | The real save: six columns in one UPDATE with the score; a failed grading; a re-grade; the old grade-all job; a manual change; the formatting job; the founder's sentence; the audit entry; the backup measurement |
| `students/tests_grading_label_not_exposed.py` | 4 | No serializer of a submission, in any app, exposes a label column |

The names the tests use, which the code must provide: on `GradingRun`, `begin_attempt()`,
`keep_answers(model, count)`, `keep_call(model)`, `keep_reused(model)`,
`keep_second_opinion(model)`, `label()` and `audit_models()`; `extract_grade_with_retry` is
handed the run by `students.services`; the audit entry's keys `models_served`, `models_reused`
and `models_second_opinion`; the measurements `model_fallback_rate` and `model_unknown_rate`.

## Expected at this commit, written before any run

All four modules load: they import only names that exist at 6ca94c09. Of the 90 tests, **81 are
expected to fail or be in error and 9 to pass.** These expectations come from reading the code,
with no run behind them; a difference is reported as a difference.

Expected to PASS today (9), each needing a mutant later unless it is a guard on a guard:
- `tests_grading_run_pipeline`: `test_the_scan_sees_a_live_read_when_there_is_one` and
  `test_every_allowed_file_exists` (guards on the static check).
- `tests_grading_label_written`: `test_a_grading_that_fails_leaves_no_label`;
  `test_a_teachers_change_of_the_score_does_not_touch_the_label`;
  `test_no_fresh_call_gives_no_sample`.
- `tests_grading_label_not_exposed`: all four.

Expected to FAIL or be in error (81): every other test. By module: all 37 of
`tests_grading_run_label` (the run has no such methods yet); 18 of `tests_grading_run_pipeline`;
26 of `tests_grading_label_written`.

### One test more, still tests only (a second commit before any code)

`AShortPaperTest.test_a_grader_named_inside_the_reply_does_not_reach_the_label`, added while
mapping the tests to the SM's rulings: no test of the first commit showed that a grader named
inside the AI's own reply cannot reach the label. Expected to be in error today, like its
neighbours. So: 91 tests, 82 expected to fail, 9 to pass; `tests_grading_run_pipeline` has 21.

## Runs

None yet.
