# BE-I-04 slice B: when a saved AI answer may be reused

Author: the Next-stage Builder, 2026-10-06. Branch `task/epic-i-be-i-04-b`, first off slice A's
frozen tip 58326e45, then on `phase2/epic-a` cfe55a0f, which holds slice A as merged (base update
9827f87a, a clean merge; against cfe55a0f the branch differs outside this folder in seven files,
all under `ai_processor/`).
Phase 2 line only. Verifier: the Next Stage Checker, independent.
Design: `~/Documents/Projects/GAP-planning/BE-I-04-design-note.md`, with the SM's rulings.

## Order of work (SM ruling: red tests are their own commit first)

1. **This commit: the tests only.** `ai_processor/tests_grading_cache_key_v2.py`, 31 tests, and
   this file. No production code is changed by it.
2. Then the code, in later commits.
3. The red run of these tests against the code as at this commit is logged before the code's
   own gate, as that gate's first step.

## Expected at this commit, written before any run

The module loads (it imports only names that exist today). Of its 31 tests, 24 are expected to
fail or be in error and 7 to pass.

Expected to FAIL (24):

`WhatMustMatchTest` (10 of 12):
- `test_edited_teacher_instructions_are_a_fresh_grade`
- `test_teacher_instructions_added_where_there_were_none`
- `test_an_edited_assignment_title_is_a_fresh_grade`
- `test_edited_assignment_instructions_are_a_fresh_grade`
- `test_a_changed_additional_note_on_the_question_is_a_fresh_grade`
- `test_a_changed_blooms_level_on_the_question_is_a_fresh_grade`
- `test_a_changed_question_image_is_a_fresh_grade`
- `test_a_field_of_the_question_nobody_listed_is_part_of_the_match`
- `test_a_changed_grading_setting_is_a_fresh_grade`
- `test_a_changed_grading_prompt_is_a_fresh_grade`

`OneReadingPerRunTest` (2 of 3):
- `test_the_settings_are_read_exactly_once_for_a_run`
- `test_a_retried_run_still_reads_them_once`

`TheSavedAnswersEnvelopeTest` (5 of 5):
- `test_the_model_that_answered_is_stored_beside_the_evaluation`
- `test_a_reply_that_names_no_model_is_stored_with_none`
- `test_the_keys_are_version_two`
- `test_an_entry_that_is_not_an_envelope_is_a_miss`
- `test_a_reused_answer_is_marked_by_our_code_not_by_its_content`

`AMarkerInsideTheReplyIsNeverKeptTest` (3 of 3):
- `test_a_graded_by_in_the_reply_is_replaced_by_the_model_that_answered`
- `test_a_from_cache_in_the_reply_is_dropped_and_the_answer_is_stored`
- `test_no_place_in_the_grading_service_keeps_a_replys_own_graded_by`

`TheTemperatureIsPartOfTheSettingsVersionTest` (4 of 4):
- `test_it_is_a_named_constant_of_the_grading_service`
- `test_the_call_that_leaves_the_app_uses_the_constant`
- `test_it_is_in_the_settings_version`
- `test_changing_it_changes_the_version`

Expected to PASS today (7). They hold behaviour the change must keep, and each needs a mutant
later to show it has teeth:
- `WhatMustMatchTest.test_the_same_submission_again_is_reused`
- `WhatMustMatchTest.test_another_assignment_with_the_same_question_is_a_fresh_grade`
- `WhatMustNotBreakTheMatchTest.test_a_new_release_still_reuses_the_saved_answer`
- `WhatMustNotBreakTheMatchTest.test_teacher_instructions_that_are_switched_off_do_not_count`
- `WhatMustNotBreakTheMatchTest.test_whitespace_around_teacher_instructions_does_not_count`
- `WhatMustNotBreakTheMatchTest.test_the_other_questions_are_not_part_of_the_match` (the stated
  limit)
- `OneReadingPerRunTest.test_a_setting_changed_during_the_call_does_not_split_lookup_and_store`

These expectations come from reading today's code, with no run behind them. Where the red run
differs, the difference is reported as a difference.

## The code (commits after 17890114)

| Piece | File |
|---|---|
| The key, version two, and the envelope | `ai_processor/grading_cache.py` |
| What a run carries: one reading of the settings and the prompt version | `ai_processor/grading_run.py` (new) |
| The run started above the retry loop and passed down; the grader marker assigned, not defaulted; the temperature as a constant | `ai_processor/services.py` |
| The temperature in the settings version | `ai_processor/grading_config.py` |
| Older tests moved to the new signatures; the settings-version pin | `ai_processor/tests_grading_cache.py`, `ai_processor/tests_grading_config.py` |

- **The key** now holds: its version (`v2`), the intended model, the assignment's id, the prompt
  version, the settings version, the assignment's title and instructions, the teacher's extra
  instructions as spliced, the whole question as serialised into the prompt, and the answer with
  outer whitespace removed (as before).
- **Stated limit, also in the module:** the key does not hold the other questions, the other
  answers or the answer's place in a batch. A test pins this.
- **The release is not in the key,** so a deploy does not empty the store. A test pins this.
- **One reading per run.** `extract_grade_with_retry` starts a `GradingRun` above its retry loop.
  The lookup, the store, the teacher-instructions splice and every attempt use it. A direct
  caller of an inner function with no run gets one for that call.
- **The envelope.** A stored value is `{"evaluation": ..., "served_model": ...}`, written by our
  code. Anything else under a key is a miss. A reused answer's `graded_by` comes from the
  envelope.
- **The marker.** `graded_by` is assigned from the response our code read, and a `from_cache` in
  a fresh reply is dropped, at both places a reply is parsed (batch and single pass), before any
  later step reads them.
- **The temperature** is `AI_TEMPERATURE` in the grading service, used by the one provider call
  and read by `GradingConfig` (SM ruling). The pinned settings version changes from
  `cfg:c039947043de` to `cfg:d09ab0c0d559` for that reason. No grade carries a version yet, so no
  stored value stops matching.

## What an operator must know

**This slice empties the store of saved answers once, when it reaches a service.** Every key
changes (`v1` to `v2`), so nothing saved before is found again; old entries expire by their own
lifetime (three days by default). Until the store refills, more answers are sent to the AI, and
AI cost rises for a while. The founder's representative accepted this (decision 3). Its size
cannot be worked out without live figures. Nothing else changes for users or the frontend.

## Limits

- **A limit of release 1 (slices A and B without C), by the SM's ruling.** The saved-answer key
  comes from the run's one reading of the settings. Of the grading code itself, only the
  teacher-instructions switch reads that reading in this slice; the other fifteen places still
  read their setting live, and move to the reading in slice C. In a running service the two
  cannot differ: these settings are taken from the environment when the process starts and
  change only at a restart, so within one process the live value and the reading are the same.
  They can differ only where something changes a setting inside a living process, which the
  tests do on purpose and the service does not.
- The key's intended model is still the main model, not the one that answered. The envelope
  records the one that answered.
- A provider named `llm` could not be told from "not named". No such model name is known.
- The answer is matched with outer whitespace removed, as before; inner differences count.
- Nothing is labelled yet; the envelope's `served_model` is first read in slice C.

## One test added with the code

`TheSpliceUsesTheRunsReadingTest.test_a_switch_flipped_after_the_run_started_is_not_seen` was
not in the red commit. I found, while writing the mutants, that no test would catch the splice
reading the live switch. It is expected to be in error in the red run (it imports the new
module), so the red run should show **25** failing: the 24 named above and this one.

## Expected for the gate, written before any run

- Step 0, the red run (the test module against the code as at 17890114): non-zero exit, a "Ran"
  line, 32 tests, the 25 failing as above and the 7 passing as above.
- Step 1a: no model or migration is changed; makemigrations clean.
- Step 1: three changed test modules, thirteen near modules, 26 guard modules: OK.
- Step 2: 23 mutants; the failing test expected for each is `EXPECTED` in `mutate.py`.
- Step 3, its own grant: `ai_processor` and `students`, the two apps that call this code.
  **Replaced (SM ruling, 2026-10-06 17:04):** the grading tasks that call this code live in
  `assignments` too, so the regression is one full run by 0b on the frozen tip, as for slice A.
  Step 3 of the script was never run.

By the SM's ruling no gate run for this slice was asked for before the Checker's verdict on
slice A; that verdict came on 2026-10-06 and slice A was merged at 17:00.

The gate script is committed here as `run_be_i_04_b_gate.sh.txt`, a copy of
`~/Documents/Projects/GAP-builder-scripts/run_be_i_04_b_gate.sh` as it stands for the run.

This slice changes no model field, no migration and no line of `AutoGrader/settings.py`. It adds
one code constant (`AI_TEMPERATURE`) to the settings version.

## Runs

### The gate at e7d4b376: GREEN

One grant from 0b, 2026-10-06 17:15:49 to 17:21:43, step 1 of the committed gate script. One run,
all serial, 6G scope, rules 12, 13, 16, 17 and 18. Not stopped, not repeated, nothing of the
team's beside it. This was the first run of any test of this slice.

| Step | Result | Log |
|---|---|---|
| 0 the red run: the slice's test module against the code as at 17890114 | exit 1 as expected: Ran 32 tests in 0.615s, FAILED (failures=28, errors=1). **Exactly the 25 tests named beforehand** fail, compared by name by program (none missing, none unexpected); the other 7 pass | `red_run_code_as_at_red_commit.txt.gz` |
| 1a makemigrations --check | exit 0, No changes detected | `makemigrations_check.txt` |
| 1 three changed modules, thirteen near modules, 26 guards | exit 0: Ran 606 tests in 173.473s, OK. No skips | `modules_and_guards.txt.gz` |
| 2 the 23 mutants | 23 KILLED, 0 SURVIVED, 0 BROKEN | `mutation_log.txt`, `mutation_results.json`, `mutant_logs/` |

- Load average 2.64 4.27 3.90 at the start, 4.27 4.88 4.41 at the end. No test here asserts on
  the wall clock (0b read the one comparison in the benchmark module: a ratio of words).
- Step 0's 28 failures and 1 error are per sub-test: one of the 25 tests has five sub-cases. The
  one error is the test added with the code, which imports the new module.
- `modules_and_guards.txt`: "Ran" is line 10922 and "OK" line 10924 of 10925.
- 26 logs are gzipped, byte-exact, because a commit hook would alter them (trailing whitespace
  or a blank last line); each one's sha256, taken before gzipping, is in
  `gzipped_logs_sha256.txt`. `console.txt.gz` is the gate script's own output.
- After the run the source was as committed and the mutation database was dropped.
- Pattern check of the new files, by program, values never printed: no URL with a password
  part. Eighteen NAME=value matches, all in tracebacks of two files (the red run and mutant
  T1): the names are `token_count`, `node_tokens`, `single_pass_mode` and a token-budget
  constant, and each "value" is code text (letters, dots, brackets, one number). None is a
  credential.

How each mutant was judged: KILLED needs a non-zero exit, the inner run's own "Ran" line, no
test module that failed to load, and the expected test among the failing ones. All 23 inner runs
exited 1 with "Ran 73 tests"; none of the 23 logs holds a load failure. The expected names were
in the runner since 2401d461, before any run.

| Mutant | What is broken | Result | Expected test, found among the failing |
|---|---|---|---|
| K1 | the teacher's extra instructions are left out of the key | KILLED, Ran 73 tests | `test_edited_teacher_instructions_are_a_fresh_grade` |
| K2 | the assignment's title is left out of the key | KILLED, Ran 73 tests | `test_an_edited_assignment_title_is_a_fresh_grade` |
| K3 | the assignment's instructions are left out of the key | KILLED, Ran 73 tests | `test_edited_assignment_instructions_are_a_fresh_grade` |
| K4 | only the question's text is in the key, not the whole question | KILLED, Ran 73 tests | `test_a_changed_additional_note_on_the_question_is_a_fresh_grade` |
| K5 | the prompt version is left out of the key | KILLED, Ran 73 tests | `test_a_changed_grading_prompt_is_a_fresh_grade` |
| K6 | the settings version is left out of the key | KILLED, Ran 73 tests | `test_a_changed_grading_setting_is_a_fresh_grade` |
| K7 | the assignment is left out of the key | KILLED, Ran 73 tests | `test_another_assignment_with_the_same_question_is_a_fresh_grade` |
| K8 | the answer is left out of the key | KILLED, Ran 73 tests | `test_miss_on_different_answer` |
| K9 | the release is put into the key | KILLED, Ran 73 tests | `test_a_new_release_still_reuses_the_saved_answer` |
| K10 | the key takes the teacher's raw text, not the text as spliced | KILLED, Ran 73 tests | `test_teacher_instructions_that_are_switched_off_do_not_count` |
| K11 | the keys stay at version one | KILLED, Ran 73 tests | `test_the_keys_are_version_two` |
| R1 | the store takes a fresh reading of the settings | KILLED, Ran 73 tests | `test_a_setting_changed_during_the_call_does_not_split_lookup_and_store` |
| R2 | each attempt of the retry loop takes its own reading | KILLED, Ran 73 tests | `test_a_retried_run_still_reads_them_once` |
| R3 | the pipeline ignores the reading it is given | KILLED, Ran 73 tests | `test_the_settings_are_read_exactly_once_for_a_run` |
| S1 | the splice reads the live switch, not the run's reading | KILLED, Ran 73 tests | `test_a_switch_flipped_after_the_run_started_is_not_seen` |
| E1 | anything that is a dictionary is taken for an envelope | KILLED, Ran 73 tests | `test_an_entry_that_is_not_an_envelope_is_a_miss` |
| E2 | a reused answer keeps the marker inside its evaluation | KILLED, Ran 73 tests | `test_a_reused_answer_is_marked_by_our_code_not_by_its_content` |
| E3 | the envelope names the intended model, not the one that answered | KILLED, Ran 73 tests | `test_the_model_that_answered_is_stored_beside_the_evaluation` |
| E4 | a reply that names no model is stored as "llm" | KILLED, Ran 73 tests | `test_a_reply_that_names_no_model_is_stored_with_none` |
| B1 | a graded_by in the AI's own reply is kept | KILLED, Ran 73 tests | `test_a_graded_by_in_the_reply_is_replaced_by_the_model_that_answered` |
| B2 | a from_cache in the AI's own reply is kept | KILLED, Ran 73 tests | `test_a_from_cache_in_the_reply_is_dropped_and_the_answer_is_stored` |
| T1 | the provider call uses a literal temperature again | KILLED, Ran 73 tests | `test_the_call_that_leaves_the_app_uses_the_constant` |
| T2 | the temperature is left out of the settings version | KILLED, Ran 73 tests | `test_it_is_in_the_settings_version` |

## The delta after the Checker's reading (SM ruling, 2026-10-06 17:28)

**The Checker found this by reading, before any verdict.** The whole answer object is sent to
the AI (`json.dumps` of each answer: `ai_processor/services.py:2665`, `:2684`, `:3907-3917`), but
the key held only the answer's text. Two answers with the same text and a different
`answer_status` (blank against "not found in the document") or different `transcription_notes`
("partly illegible") shared a key, so the second student would get the first one's saved grade
although the AI would have been told something different. My own "the answer" in the key was the
text alone; the ruling that named "the whole question as sent and the answer" was only half
followed.

**Ruling:** the key also takes `answer_status` and `transcription_notes`. It does NOT take
`source_page`, `confidence` or the answer's own copy of `question_text`: they differ from student
to student for the same text, and matching on them would end all reuse. Those three are a stated
limit. What is sent to the AI does not change.

**Order of work, as for the slice:** the red tests are this commit, alone; then the code; then
mutants with their expected failing tests named first.

### The delta's red tests, and what is expected of them (written before any run)

`TheAnswerAsSentTest` in `ai_processor/tests_grading_cache_key_v2.py`, 12 tests. Against the code
as at 59990797:

Expected to FAIL (5):
- `test_the_same_text_with_a_different_status_is_a_fresh_grade`
- `test_the_same_text_with_different_notes_is_a_fresh_grade`
- `test_a_status_against_no_status_is_a_fresh_grade`
- `test_notes_against_no_notes_is_a_fresh_grade`
- `test_an_inner_difference_in_the_notes_is_a_fresh_grade`

Expected to PASS today (7), each needing a mutant later:
- `test_the_same_status_and_notes_are_reused`
- `test_a_different_page_and_confidence_do_not_break_the_match` (stated limit)
- `test_the_answers_own_copy_of_the_question_text_does_not_break_the_match` (stated limit)
- `test_no_status_is_one_thing_however_it_is_written`
- `test_no_notes_is_one_thing_however_it_is_written`
- `test_outer_whitespace_of_the_notes_is_not_compared`
- `test_a_status_that_is_not_text_does_not_crash_the_grading`

**How "nothing said" is handled, a decision of mine stated for the verifier:** a field that is
missing, `None`, empty or only whitespace is one and the same thing for the match. The text sent
to the AI does differ between those forms (a missing key, `null`, `""`), and I judge that none of
them tells the AI anything. Outer whitespace of the notes is not compared, as for the answer's
text; an inner difference is.

### Three more items in the same delta (SM, from the Checker's reading)

A second tests-only commit, before any code of the delta: `TheChunkedPathTest` (4 tests) and
`ThePartsOfTheKeyCannotRunTogetherTest` (5 tests) in the same module. I wrote these tests; the
Checker probes on its own. Expected against the code as at 59990797, written before any run:

Expected to FAIL (2), where the behaviour is new:
- `test_a_separator_inside_a_part_cannot_pass_for_a_boundary`: today each part is followed by a
  NUL byte, so a part ending in NUL beside an empty part gives the same bytes as the part beside
  a part that is one NUL.
- `test_the_last_part_of_the_context_and_the_answer_cannot_run_together`: the same weakness
  between the teacher's instructions and the answer.

Expected to PASS today (7); the code already does it, and a mutant with its expected failing
test named first is the proof:
- `test_the_first_long_paper_is_marked_in_parts` (a guard on the fixture)
- `test_an_identical_second_long_paper_makes_no_provider_call`
- `test_a_graded_by_in_a_chunks_reply_is_replaced`
- `test_a_from_cache_in_a_chunks_reply_is_dropped_and_all_are_stored`
- `test_a_character_moved_across_a_boundary_changes_the_key`
- `test_the_model_and_the_assignment_cannot_run_together`
- `test_the_same_parts_give_the_same_key`

A NUL cannot be stored in a text column of this database, so the weakness the two red tests show
is not one a teacher could reach today; the key is made unambiguous all the same.

### Still owed

The regression: one full run by 0b on the frozen tip (SM ruling). By the same ruling it stands as
Gate 10 for release 1 (slices A and B) if the merged epic tree equals this slice's tree. Its
result is added here by a later, evidence-only commit.
