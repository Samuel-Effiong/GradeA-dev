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

### The SM's additions: a third tests-only commit, before any code

The SM approved the mapping of these tests to its rulings on condition of these additions, and
did not accept two things I had left out (the two real entry points; running the formatting
job). All of it is tests only.

New module `students/tests_grading_label_routes.py`, 10 tests:
- **The background task** (`grade_engine_async`, run eagerly) and **the immediate route** (the
  HTTP grade route): each reads the STORED audit entry and the six columns from the database and
  checks they agree. The audit entry is emitted by those two callers, not by `grade_engine`.
- **An audit failure** does not fail the grade, and the label is on the row.
- **A re-grade that fails** (a provider failure; a cancel at the final save) leaves the first
  score with the first label. **A re-grade that succeeds** replaces the whole label (backup
  "yes" then all main gives "no"; AI then fixed-rule gives "deterministic" and
  "not_applicable").
- **Created and graded on the same in-memory instance.**
- **The formatting job, run for real** with its AI call replaced: a score and a label saved
  while that call is in flight are not written back over. The code-reading test stays beside it.

Added to the earlier modules, 7 tests:
- `tests_grading_run_label.ALongModelNameTest` (5): a name longer than an audit item (64) and
  longer than the column (255) is classified by its exact text before anything is cut; a name
  that differs from the main model or a backup only after the cut is "unknown", never "no".
- `tests_grading_label_written.TheBackupMeasurementTest.test_a_long_backup_name_is_still_a_one_in_the_rate`.
- `tests_grading_run_pipeline.TheStartOfRunReadingIsEverywhereTest` (1): a setting changes while
  the AI is answering; the label's settings version, the lookup key and the store key all show
  the reading the run started with.

### Expected at this commit, re-written before any run

**108 tests in five modules; 99 expected to fail or be in error, 9 to pass.** The 9 expected to
pass are the same nine named above. All 17 added tests are expected to fail: the route and
re-grade tests because the stand-in for the grading service is not yet handed a run; the
formatting job's because it saves the whole row today; the others because the run has no label
yet.

By module: `tests_grading_run_label` 42, all fail; `tests_grading_run_pipeline` 22, 20 fail;
`tests_grading_label_written` 30, 27 fail; `tests_grading_label_routes` 10, all fail;
`tests_grading_label_not_exposed` 4, none fail.

### A fourth audit key (SM ruling): a fourth tests-only commit, before any code

I had proposed that the backup measurement compare names cut to 64 characters, since it read the
audit lists. The SM ruled otherwise: classified before any cut. So the audit entry gets ONE more
permitted key, **`fresh_backup_used`**, holding the classification of the run's FRESH calls only
(`yes`, `no`, `unknown`, or `no_fresh_call`), worked out on the exact names by the same rule as
the label's flag. The rate reads that key; `unknown` is counted apart. The three lists stay, cut
to 64 for a person to read, and are no longer what the rate is computed from. Two names sharing
their first 64 characters cannot be confused anywhere, so there is no limit to state.

Tests changed or added, tests only:
- `tests_grading_run_label.TheFreshCallsClassificationTest` (11, new): the new method
  `GradingRun.fresh_backup_used()`.
- `tests_grading_label_written.TheBackupMeasurementTest` rewritten (9): the rate from the key;
  not from the lists; an entry from before the key still measured by its one `model`.
- The audit-entry tests and the two route tests also check the key.

### Expected at this commit, re-written before any run

**119 tests in five modules; 108 expected to fail or be in error, 11 to pass.** Two more pass
today than before, both in the rewritten measurement class, because they describe what the old
code already does: `test_lists_without_the_key_give_no_sample_from_the_lists` and
`test_an_entry_from_before_the_key_is_still_measured_by_its_one_model`. The other nine are the
nine named above (`test_no_fresh_call_gives_no_sample` now passes the key's fixed word).

By module: `tests_grading_run_label` 53, all fail; `tests_grading_run_pipeline` 22, 20 fail;
`tests_grading_label_written` 30, 25 fail; `tests_grading_label_routes` 10, all fail;
`tests_grading_label_not_exposed` 4, none fail.

### Five more tests, still tests only (a fifth commit before any code)

- `tests_grading_label_written.TheTwoPlacesThatEmitTheEntryTest` (3): the SM accepted that an
  audit entry with no `fresh_backup_used` is measured the old way, for OLD entries only, on
  condition that no production caller emits a grading entry without a run. These read the code:
  the entry is emitted in exactly two places (the background task and the immediate route); each
  hands over what `grade_engine` returned; and that instance carries the run.
- `tests_grading_run_label.TheWordsAreTheLabelsWordsTest` (2): the run keeps its own copy of the
  label's words (the AI code does not import the students app); they must equal the label's, and
  its cut for an audit item must equal the audit limit.

### Expected at this commit, re-written before any run

**124 tests in five modules; 111 expected to fail or be in error, 13 to pass.** Of the five
added, two pass today because the two callers already exist as described
(`test_the_entry_is_emitted_in_exactly_the_two_callers`,
`test_each_hands_over_what_grade_engine_returned`); the other three fail. The other eleven
expected to pass are those named above.

By module: `tests_grading_run_label` 55, all fail; `tests_grading_run_pipeline` 22, 20 fail;
`tests_grading_label_written` 33, 26 fail; `tests_grading_label_routes` 10, all fail;
`tests_grading_label_not_exposed` 4, none fail.

## The code (the commit after the five tests-only commits)

| Piece | File |
|---|---|
| What a run gathers and derives: `keep_answers`, `keep_call`, `keep_reused`, `keep_second_opinion`, `begin_attempt`, `label`, `audit_models`, `fresh_backup_used` | `ai_processor/grading_run.py` |
| The run's attempt reset; the points where a kept reply is recorded; every grade-shaping setting read through `_grading_setting(run, name)` | `ai_processor/services.py` |
| The run started above the grading service; the six columns set and saved by the same UPDATE as the score; the audit entry's keys | `students/services.py` |
| Four keys permitted on the grading entry | `audit/metadata.py` |
| The backup measurement reads `fresh_backup_used` | `audit/emitter.py` |
| The formatting job saves only its own field | `assignments/tasks.py` |
| The "nothing said" choice among the stated limits (carried from slice B, accepted by the SM) | `ai_processor/grading_cache.py` (docstring), `docs/phase2/architecture/03a_data_model.md` |
| The label, the rules of the flag and the audit entry's keys | `docs/phase2/architecture/03a_data_model.md` |
| The one changed expression stated exactly (the Checker's note on slice A) | `students/tests_grading_label_migration.py` (docstring only) |

- **Where a reply is "kept".** A fresh reply is recorded at the point where its evaluations are
  marked with the model our code read from the response, which is after every check that can
  reject it. A second-opinion reply, which goes through the same batch function, is recorded
  apart. The summary call is recorded where its result is accepted. A reused answer is recorded
  when the store returns it, by the model the envelope names. Every attempt starts by emptying
  what was gathered.
- **The settings.** Fourteen live reads in the grading service became reads of the run's one
  reading. A helper called with no run takes a reading then. The two reads in the offline
  benchmark command stay, allowed by name in the static test with the reason.
- **The audit entry** says what the label says when the submission carries a label and a run;
  its `model` is the label's model. An entry from a submission with no run is written as before
  and measured by its one `model`: for old entries only. Both production callers hand over what
  `grade_engine` returned.
- **No model, no migration, no settings line changed.**

## Stated limits

- The label says which models produced the grade that was saved, not every model that was
  called: a rejected reply and a failed attempt are in the per-call log line only.
- The rule for dated or suffixed model names waits for the approved live test (SM ruling): a
  provider name that differs from the configured main or backup name by a suffix reads
  "unknown" today.
- A re-grade replaces the label, as it replaces the score. The first form of the record keeps
  no history.
- Behaviour of the settings reading is shown for two settings and one mid-run switch; the other
  twelve rest on the static check.
- The audit entry's `model` is cut to 128 characters and the lists' names to 64, for reading.
  The row holds 255. Nothing is classified from a cut name.

## Runs

None yet.
