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
- Behaviour of the settings reading is shown for two settings and one mid-run switch. The
  others rest on TWO static checks, and on no more than these (corrected 2026-10-07 after the
  Checker's finding 3: the first check alone does not see a call that drops the run, because
  such a call reads live through the helper): (a) no file outside the settings-version module
  reads a grade-shaping setting from Django settings; (b) every call inside the grading service
  to one of thirteen named methods that take the run hands the run on by name. What neither
  sees: a NEW method that takes the run and is not added to the list in (b); and a caller
  outside `ai_processor/services.py` that calls one of them without a run.
- The votes for the grade's model are counted over the items of each fresh reply as received;
  repeated items are not removed (SM ruling 2026-10-07; stated in `GradingRun._grading_model`
  and in 03a). The root is a beta-line row of its own, H-154 (the Hardening Engineer's), not
  this slice's.
- The audit entry's `model` is cut to 128 characters and the lists' names to 64, for reading.
  The row holds 255. Nothing is classified from a cut name.

## The mutants (expected failing test named before any run)

52 in `mutate.py` (51 as first committed, and S13 added on 2026-10-07 with the carried test,
below), each with one expected failing test in `EXPECTED`: R1 to R20 on the run's
rules (`ai_processor/grading_run.py`); S1 to S12 on what the grading service keeps and on its
settings reads; S13 on the store's skip of a reused answer; T1 to T10 on the save and the audit entry (`students/services.py`); A1 to A4 on
the permitted keys and the measurement; F1 the formatting job; M1 a teacher's manual change; X1 a
serializer; E1 a third place emitting the entry; and **V1, the reverse half of slice A's
migration test** (the Checker's note on slice A): the migration's way back spoils the grade, on a
database built fresh.

**One test was strengthened after the code commit and before any run,** and I say so plainly:
`test_one_call_marking_three_answers_votes_three_times`. As first written (one reused answer by a
backup against three fresh ones by the main model) it would have passed even with mutant S3 (a
reply votes once, not once per answer): one vote each is a tie, and the tie rule gives the main
model. It now sets two reused answers by a backup against three fresh ones in one call, so a
per-call count names the backup. I found it while writing S3's expected test. It is still
expected to fail in the red run, like its neighbours.

No mutant is offered for the guards on the guards (the two scan guards, the serializer walk's own
guard) or for `test_a_grader_named_inside_the_reply_does_not_reach_the_label`: the label is built
only from what the run kept, and the run is fed only the model our code read from the response,
so there is no single line to break that leaves the rest standing; slice B's mutants B1, B2, C2
and C3 are the ones that put a reply's own marker back.

## Rule 19: each expected kill, re-read (can-fail table)

Written 2026-10-07, after the base update (below) and before any run. Slice C's nine mutated
files are the same on the new base as at a12bc6bc: the base update changed, outside the
evidence folders, only `ai_processor/grading_cache.py` (two docstring lines),
`ai_processor/tests_grading_cache_key_v2.py`, the Checker's test module and 03a.
`mutate.py --check` on the new base: 52 mutants, every anchor found once, all parse.

Every row is BY READING: nothing of slice C has run. "Fails because" says
what the named test's assertion sees under the mutant. The gate's battery is
the run that shows each red.

| Mutant | Expected failing test | Fails because (by reading) |
|---|---|---|
| R1 | test_the_summary_call_does_not_vote | 5 backup summary calls outvote 1 main answer: model reads backup, test wants main |
| R2 | test_fresh_and_reused_answers_are_counted_together | 3 reused backup answers no longer vote: model reads main, test wants backup |
| R3 | test_a_two_way_tie_goes_to_the_main_model | 5:5 tie falls to the alphabet: "backup/model" before "main/model" |
| R4 | test_a_tie_without_the_main_model_goes_to_the_first_by_alphabet | reversed order gives "zeta/model", test wants "alpha/model" |
| R5 | test_unknown_loses_a_tie_to_a_named_model | 4:4 tie with an unnamed model reads "unknown", test wants "zeta/model" |
| R6 | test_any_backup_answer_is_yes | 9 main + 1 backup is not all-backup: flag reads "unknown", test wants "yes" |
| R7 | test_main_with_an_unnamed_model_is_unknown | the unnamed answer is passed over: flag reads "no" |
| R8 | test_main_with_a_model_on_neither_list_is_unknown | the other model is passed over: flag reads "no" |
| R9 | test_a_reused_answer_first_made_by_a_backup_is_yes | reused backup answer left out: flag reads "no" |
| R10 | test_a_backup_summary_call_alone_is_yes | backup summary call left out: flag reads "no" |
| R11 | test_it_is_in_neither_the_model_nor_the_flag | 9 backup second opinions counted: flag reads "yes", test wants "no" |
| R12 | test_no_ai_call_and_nothing_reused_is_not_applicable | empty run reads "no" |
| R13 | test_a_new_attempt_starts_from_nothing | 5 backup answers of the old attempt stay: flag "yes" and backup among the served |
| R14 | test_a_reused_backup_answer_does_not_make_the_fresh_calls_yes | reused backup counted as fresh: "yes", test wants "no" |
| R15 | test_a_backup_name_longer_than_an_audit_item_is_still_yes | the 150-character name cut to 64 is not on the backup list: flag "unknown" |
| R16 | test_a_long_model_name_is_cut_to_the_column_not_refused | 400 characters returned, test wants 255 |
| R17 | test_the_lists_are_read_from_the_runs_reading_not_live | live main is "another/main": the kept main answer reads "unknown", test wants "no" |
| R18 | test_a_model_name_shaped_like_an_address_is_not_dropped_silently | "@" still in the served item |
| R19 | test_an_unnamed_model_is_the_explicit_word_unknown | lists hold "" instead of "unknown" |
| R20 | test_the_versions_and_the_release_come_from_the_runs_reading | release read live is "release-43", test wants "release-42" |
| S1 | test_a_whole_attempt_that_failed_leaves_nothing_behind | attempt one's backup part stays: flag "yes", backup among the served |
| S2 | test_marked_by_a_backup_model | nothing kept: model "deterministic", test wants the backup |
| S3 | test_one_call_marking_three_answers_votes_three_times | one vote for main against two reused backup answers: model reads backup |
| S4 | test_a_backup_marking_one_part_is_seen | no part kept, only the summary call: model "deterministic" |
| S5 | test_the_part_with_the_most_answers_names_the_model | parts of 10 (backup) and 2 (main) vote 1:1, tie goes to main; test wants backup |
| S6 | test_its_model_is_in_its_own_list_and_nowhere_else | second opinion kept as an answer: flag "unknown", second model among the served |
| S7 | test_a_backup_summary_call_is_flagged_but_does_not_name_the_model | summary call not kept: flag "no", served without the backup |
| S8 | test_a_wholly_reused_paper_names_the_model_that_first_answered | nothing kept: model "deterministic" |
| S9 | test_a_reused_answer_whose_first_model_was_not_named_is_unknown | the store's word "llm" kept as a name: model reads "llm" |
| S10 | test_fixed_rule_marking_stays_on_for_a_run_that_started_with_it_on | live switch is off: the objective answer goes to the AI, call count is not 0 (or the stand-in reply errors: the test is then an ERROR, which the judge counts) |
| S11 | test_a_second_opinion_switched_on_mid_run_is_not_asked_for | live switch is on: a second call is made (rests on the default second-opinion list being non-empty and the 20-point question passing the default threshold of 15, both read in AutoGrader/settings.py) |
| S12 | test_nothing_outside_the_settings_version_module_reads_one | the scan finds the getattr in ai_processor/services.py |
| S13 | test_the_store_skips_an_answer_marked_as_reused | the store is called for answers 1 and 2, test wants 2 only (NEW, see below) |
| T1 | test_the_six_columns_after_a_grading_by_the_main_model | the six columns are not in the UPDATE: the row reads "unlabelled" |
| T2 | test_the_grading_service_is_handed_a_run | the stand-in reads kwargs["run"]: KeyError (an ERROR of that test) |
| T3 | test_a_backup_model_is_flagged | the save uses a new empty run: "deterministic" / "not_applicable" |
| T4 | test_the_label_and_the_score_are_one_update | two UPDATEs set a label column, only one sets the score |
| T5 | test_it_carries_the_three_lists_of_models | metadata has no "models_served" (KeyError) |
| T6 | test_an_entry_is_stored_with_the_lists | stored metadata has no "fresh_backup_used" (KeyError, after the three list assertions pass) |
| T7 | test_its_model_is_the_labels_model | model falls back to the feedback's, which has none: None, test wants the backup |
| T8 | test_it_carries_the_settings_version_and_the_strictness | metadata has no "grading_config_version" (KeyError) |
| T9 | test_what_grade_engine_returns_carries_the_run | the assignment is no longer in the function's source |
| T10 | test_the_function_that_writes_the_label_says_it | the docstring no longer holds "first form of the grading record" |
| A1 | test_the_three_lists_are_permitted_for_this_entry_and_pass_validation | "models_reused" not in the entry's permitted keys |
| A2 | test_unknown_is_counted_apart_and_not_in_the_rate | samples are {model_fallback_rate: 0.0}, test wants {model_unknown_rate: 1.0} |
| A3 | test_no_fresh_call_gives_no_sample | a 0.0 sample is given, test wants none |
| A4 | test_the_old_single_model_does_not_override_the_key | the main model's 0.0 is written after the key's 1.0 |
| F1 | test_a_score_and_label_saved_meanwhile_are_not_written_back_over | the whole-row save writes the old score and the placeholder label back |
| M1 | test_a_teachers_change_of_the_score_does_not_touch_the_label | grading_model reads "manual", test wants the kept value |
| X1 | test_no_submission_serializer_has_a_label_field | StudentSubmissionGradeUpdateSerializer lists grading_model |
| E1 | test_the_entry_is_emitted_in_exactly_the_two_callers | a third caller, grade_all_submissions, is found |
| V1 | test_a_row_made_before_0031_reads_the_placeholder_after_it | going back sets score to 0, test wants 7.0 |

Changed by the re-read: none of the 51. Added: S13 with one new test.

### The carried test: a reused answer is not stored a second time

Two defences hold this. The grading service hands the store only what the
run freshly marked (`fresh_evaluations`, taken before the reused answers
are merged in); and `_store_cache_evaluations` itself skips an evaluation
marked `from_cache`. With both in place, no single break of the first can
be seen from outside, so an end-to-end test of it could not be shown red
and is NOT added (rule 19). The second defence is held alone:

- test `test_the_store_skips_an_answer_marked_as_reused`
  (`ai_processor/tests_grading_run_pipeline.py`): the store routine is
  called directly with one answer marked as reused and one fresh; the store
  must be called for the fresh one only. The fresh one shows the routine
  was reached.
- mutant S13: the `from_cache` half of the skip is removed.

The behaviour is slice B's, so the test PASSES in the red run: 125 tests,
111 fail, 14 pass.

## The base (2026-10-07)

The branch was moved onto the merged line by the Release Engineer's merge 142a5040 (parents
a12bc6bc and `phase2/epic-a` 441c0e70, which is the merge of slice B, 82c3108d, plus the record
of the release full run: 6498 OK). Two files were resolved as I had prepared them and compared
byte for byte: 03a (my side of one conflict) and `ai_processor/grading_cache.py` (git merged it
without a conflict and doubled one docstring paragraph; one copy removed, so the file equals the
merged line's). The gate's step 1 now also runs the Checker's module
`ai_processor.tests_grading_cache_key_v2_checker`, which came with the base.

## Expected for the gate, written before any run

- Step 0, the red run: the five new test modules against the seven code files as at 9c0370f1:
  non-zero exit, a "Ran" line, **125 tests, 111 failing or in error and 14 passing**: the 124
  named above (111 and 13) and the carried test of 2026-10-07, which passes there because the
  behaviour is slice B's.
- Step 1a clean (no model, no migration changed). Step 1 OK. Step 2: 52 KILLED.
- The regression is 0b's one full run on the frozen tip (SM ruling).

The gate script is committed here as `run_be_i_04_c_gate.sh.txt`. Slice B is merged and this branch is on that merge
(above), so the gate is now asked for.

## Runs

### The gate at 8f192dd5, 2026-10-07 (the Release Engineer's grant; one run)

Started 11:37:40 WAT, ended 11:58:44, exit 0. Load 5.78 at the start, 7.75 at the end (another
short run went beside the mutants, on the Release Engineer's grant).

- **Step 0, the red run** (code as at 9c0370f1): exit 1, `Ran 125 tests in 7.706s`,
  `FAILED (failures=29, errors=91)`. The 120 counts entries: two tests report their sub-checks one
  by one (7 and 4 entries). By distinct test: **111 failing or in error, 14 passing, as
  expected.** Log: `red_run_code_as_at_red_commit.txt.gz`.
- **Step 1a:** `No changes detected` (`makemigrations_check.txt`).
- **Step 1:** `Ran 891 tests in 227.004s`, `OK` (`modules_and_guards.txt.gz`).
- **Step 2:** **52 KILLED, 0 SURVIVED, 0 BROKEN** (`mutation_log.txt`, `mutation_results.json`,
  one log per mutant in `mutant_logs/`). Each has its "Ran" line and its named expected test
  among the failing tests; none timed out. The can-fail table's rows are now each seen red in a
  run. The source was clean after the mutants and the mutation database was dropped.

A first attempt at 11:36:43 did not run: my command left out `bash` and the script file is not
executable, so the shell refused it in four seconds (exit 126). No test started. Its two lines are
kept as `console_first_attempt_no_run.txt`. I did not start it again myself; the run above is on
a second grant for the corrected command.

Logs: the console, the two big logs and the mutant logs are gzipped byte for byte (before
staging; the whitespace hook would alter them); `gzipped_logs_sha256.txt` holds the sha256 of each
unpacked file. Checked by program before the commit, counts only: no line with a password inside
an address, none with the word "secret"; 16 lines match a NAME=value shape and all are sub-check
labels of the form key='models_served'.

The regression is the Release Engineer's one full run on the frozen tip; its log is committed
here when it is done.

## The delta after verification (2026-10-07)

The Next-stage Checker's verdict at d37f6a7e: VERIFIED-WITH-NOTES, five items required before
merge. Its record is committed here byte for byte as `VERIFICATION_be_i_04_slice_c.md`
(sha256 d4b98836...bbbc82). The delta is one set of two commits, tests first.

**Commit 6d28cc0f, tests only.** Two modules adopted from the Checker's hand-over files
(`handover_slice_c_probes_ai_processor.py` fe6f3a04..., `handover_slice_c_probe_route.py`
a615c03a...): `ai_processor/tests_grading_run_checker.py` (PC1, PC2, PC3, PC4, PC6: 8 tests) and
`students/tests_grading_label_end_to_end.py` (PC0: 1 test). Changed from the hand-over only by
the formatter's line wrapping, one `dict(...)` written as a literal (the lint hook), the module
docstrings and one docstring line of PC6. PC5 is NOT adopted (the Checker's word: it belongs to
the other row and went red at another assertion than the one it was written for).

**The second commit, code and words.**

| Item | What was done |
|---|---|
| 1. The unknown rate (ruling; behaviour changes) | `audit/emitter.py`: a grading whose fresh calls read "yes" now gives `model_unknown_rate` a 0 as well as `model_fallback_rate` a 1. The comment there and a note in 03a state the two bases, that the unknown rate means "backup use not knowable" and not "a model not named", and the third case (no fresh call: no sample to either). Test: PC6, written before the code. |
| 2. A rejected reply that holds answers | No code change (the Checker: the code is right). Test: PC1. |
| 3. A check that sees a dropped run | No code change. Test: PC2 (two tests). The stated limit above is corrected. |
| 4. One test from a real entry point to the row and the entry | No code change. Test: PC0. |
| 5. The vote count, stated | No code change (ruling). `GradingRun._grading_model`'s docstring, 03a and the stated limits above say that votes are counted over the reply's items as received, with the pointer to the beta-line row (opened by the Senior Manager on 2026-10-07, owner the Hardening Engineer; I do not have its number yet). |

Offered and adopted though not required: PC3 and PC4 (both tests of each). The five smaller
points of the record are not taken up in this delta.

### The mutants of the delta, and rule 19

`mutate.py` now holds 58: the 52 of the first gate, unchanged, and six more. It can now apply a
break that needs two edits of one file (S14, S17); every `old` text must still be found exactly
once, and a break that changes nothing is refused. Every mutant now also runs the two adopted
modules. `--check` at this commit: 58 mutants, every anchor found once, all parse.

| Mutant | The break | Expected failing test | Fails because | Seen red |
|---|---|---|---|---|
| A5 | "yes" gives the unknown rate no sample (the tip's behaviour before this delta) | test_yes_no_and_unknown_each_give_one_unknown_sample | samples for "yes" lack `model_unknown_rate` 0.0 | by the Checker's run (red at the tip d37f6a7e); mine: the red run of this gate, then the battery |
| S14 | a part's reply is counted before its evidence check | test_a_backups_reply_rejected_for_its_quote_leaves_nothing | the rejected backup reply's ten answers stay: flag "yes", test wants "no" | by the Checker's run (its P1, the same two edits); mine: by reading until this gate |
| S15 | one site reads the evidence mode without the run | test_no_call_inside_the_grading_service_drops_the_run | the scan lists `_evidence_mode` at that line, test wants an empty list | by the Checker's run (its P2, the same edit); mine: by reading until this gate |
| S16 | a reused answer is counted as the main model's | test_a_backup_answers_then_a_second_student_reuses_it | the second student's row names the main model, test wants the backup | by the Checker's run (its P7, the same edit); mine: by reading until this gate |
| S17 | the second-opinion model is recorded when asked, not when its reply is kept | test_a_second_opinion_that_failed_leaves_no_model | the failed second opinion's model is in its list, test wants an empty list | by the Checker's run (its P5, the same two edits); mine: by reading until this gate |
| R21 | the rate's word passes over an unnamed model | test_every_mix_of_fresh_calls | one unnamed answer reads "no" for the rate and "unknown" for the flag | by the Checker's run (its P3, the same rule); mine: by reading until this gate |

Each adopted test, and the break that fails it: PC6 by A5; PC1 by S14; PC2's first test by
S15 (its second test is the guard on that scan and has no mutant); PC3's first test by R21 (its
second test is the guard that the words are not all one word; no mutant); PC4's first test by
the existing S6, which is the Checker's P4 in other words (a second opinion kept as the
grader's answer) and whose named expected test stays my own; PC4's second test by S17; PC0 by
S16, and by the existing T2 and T3 (the Checker's P8 and P6), whose named expected tests stay
my own. Whether PC4's first test and PC0 are among the failing tests under S6, T2 and T3 is
read from the battery's results and written under "Runs".

### Expected for the delta's gate, written before any run

Script `run_be_i_04_c_gate_delta.sh` (committed copy `run_be_i_04_c_gate_delta.sh.txt`): the
first gate's script with the red commit, the red run's modules, two modules more in step 1, the
count and the output file names changed.

- Step 0, the red run: the two adopted modules against the seven code files as at 6d28cc0f:
  non-zero exit, a "Ran" line, **9 tests, 1 failing
  (`test_yes_no_and_unknown_each_give_one_unknown_sample`), 8 passing.**
- Step 1a: no changes. Step 1: OK; by count 900 tests (the 891 of the first gate and these 9).
- Step 2: **58 KILLED**, each with its "Ran" line and its named test.
- The regression: the Release Engineer's ONE full run on the final tip.

### The delta's gate at 37af761b, 2026-10-07 (the Release Engineer's grant; one run)

Started 13:01:35 WAT, ended 13:29:25, exit 0 (`console_stamp_delta.txt`). Load 4.39 at the start,
5.47 at the end (another gate ran beside it, on the Release Engineer's grant).

- **Step 0, the red run** (the two adopted modules, code as at 6d28cc0f): exit 1,
  `Ran 9 tests in 0.993s`, `FAILED (failures=1)`. The one failing test is
  `test_yes_no_and_unknown_each_give_one_unknown_sample`; 8 pass, **as expected.** Log:
  `red_run_code_as_at_red_commit_delta.txt.gz`.
- **Step 1a:** `No changes detected` (`makemigrations_check_delta.txt`).
- **Step 1:** `Ran 900 tests in 306.028s`, `OK` (`modules_and_guards_delta.txt.gz`).
- **Step 2:** **58 KILLED, 0 SURVIVED, 0 BROKEN** (`mutation_log_delta.txt`,
  `mutation_results_delta.json`, one log per mutant in `mutant_logs_delta/`). Checked by program:
  each entry has its "Ran" line (134 tests; 1 for V1) and its named expected test among the
  failing tests; none has exit 0 or 124. The six new rows (A5, S14, S15, S16, S17, R21) are now
  each seen red in a run of mine. The source was clean after the mutants and the mutation
  database was dropped.
- **The three older mutants and the adopted tests, read from the results:** under S6,
  `test_it_sets_neither_the_flag_nor_the_rates_word` (PC4's first test) is among the failing
  tests; under T2 and under T3, `test_a_backup_answers_then_a_second_student_reuses_it` (PC0) is
  among the failing tests.

Logs: the console, the two big logs and the 58 mutant logs are gzipped byte for byte before
staging; `gzipped_logs_delta_sha256.txt` holds the sha256 of each unpacked file. Checked by
program before the commit, counts only: no line with a password inside an address, none with
the word "secret"; 9 lines match a NAME=value shape (8 in the log of A1, 1 in the modules log)
and all are sub-check labels of the form keys=name or key='name', letters and one underscore.

The row for the vote's root is H-154 (beta line, the Hardening Engineer). The number is in this
file and in 03a; the comment in `GradingRun._grading_model` keeps its words without the number,
on the Senior Manager's word (the code is as verified).

Next: the Checker verifies this delta; then the Release Engineer's one full run on the final tip.
