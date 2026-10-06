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

## Runs

None yet.
