# BE-I-04 slice B: when a saved AI answer may be reused

Author: the Next-stage Builder, 2026-10-06. Branch `task/epic-i-be-i-04-b`, off slice A's frozen
tip 58326e45 (itself off `phase2/epic-a` 9a581258); a base update onto A's merge follows.
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

- Only the teacher-instructions switch is read from the run's reading in this slice. The other
  fifteen places the grading code reads a setting still read it live; they move in slice C.
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
- Step 1: three changed test modules, twelve near modules, 26 guard modules: OK.
- Step 2: 23 mutants; the failing test expected for each is `EXPECTED` in `mutate.py`.
- Step 3, its own grant: `ai_processor` and `students`, the two apps that call this code.

By the SM's ruling no gate run for this slice is asked for before the Checker's verdict on
slice A.

## Runs

None yet.
