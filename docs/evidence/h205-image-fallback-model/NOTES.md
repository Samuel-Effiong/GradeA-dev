# H-205 notes

- **What changed** (the owner approved the plan: cheap, accurate, zero-retention, image-capable; the Senior
  Manager's ruling and design, 24 October 2026):
  - For `grade_assignment`, `extract_answer` and `extract_assignment`, a request that carries an `image_url`
    part falls back to `IMAGE_FALLBACK_MODELS = ["anthropic/claude-haiku-5.5"]`.
  - A text-only request keeps `GRADING_FALLBACK_MODELS = ["deepseek/deepseek-v4-pro"]` exactly as before.
  - The model pin (the second grader) still wins. Never a mixed list.
  - `GRADING_SECOND_OPINION_MODELS` defaults to deepseek, then claude-haiku-5.5, replacing gemini-2.5-flash,
    which has no zero-retention endpoint.
- **Evidence for the choice:** H-205 run1, 24 October 2026, 12 scanned papers, every call under `zdr: true`.
  claude-haiku-5.5 read 238/240 multiple-choice letters, graded with MAE 0.556 against the teacher (grok-4.3:
  0.626), had 0 errors, and cost about $0.27 for reading and grading all 12. It was the cheapest model tested.
- **`pdf_url` parts alone are not images:** no production code builds them (PDFs become page images first).
  A control test pins this.
- **Effect after deploy (accepted by the Senior Manager):** `IMAGE_FALLBACK_MODELS` joins both config versions.
  The grading config version changes, so saved grading answers are not reused for one round. New readings carry
  a new `rcfg:` version.
- **Pinned grading version:** `cfg:d09ab0c0d559` becomes `cfg:49188dfc9eb4` in `tests_grading_config`. It was
  computed outside Django from the pinned tables, and the same computation reproduces the old pin exactly first.
- **Not in this row:** H-254. A second-opinion request can carry question images, and with deepseek as grader B
  that pass fails and the paper stands unflagged (`services.py`, the broad `except` after the second opinion).
  The owner decides; no change here.
- **Correction to the proposal:** `tests_vote_helper` and `tests_grading_run_label` needed no edit. They patch the
  `services` constants and the grading run reads its config live.

## Expected reds against dac16545 (written before any run)

```
H-205 written reds: each test below against the code at dac16545 (no IMAGE_FALLBACK_MODELS, no image
branch, settings default with gemini-2.5-flash). Written 2026-10-24 before any run. Fragment = text that
must appear inside that test's own FAIL/ERROR block.

ai_processor.tests_h205_image_fallback
  AnImageCallFallsBackToHaikuTest.test_an_image_in_the_user_prompt_of_each_page_task
      "['deepseek/deepseek-v4-pro'] != ['anthropic/claude-haiku-5.5']"
  AnImageCallFallsBackToHaikuTest.test_an_image_only_in_a_message
      "['deepseek/deepseek-v4-pro'] != ['anthropic/claude-haiku-5.5']"
  AnImageCallFallsBackToHaikuTest.test_an_image_only_in_a_list_system_prompt
      "['deepseek/deepseek-v4-pro'] != ['anthropic/claude-haiku-5.5']"
  TheRequestThatLeavesTest.test_an_image_call_sends_haiku_as_the_only_backup
      "['deepseek/deepseek-v4-pro'] != ['anthropic/claude-haiku-5.5']"
  TheListTest.test_the_image_backup_is_haiku_and_never_a_nano_model
      "has no attribute 'IMAGE_FALLBACK_MODELS'"
  ABackupAnsweredTest.test_a_grading_answered_by_haiku_says_a_backup_was_used
      "'unknown' != 'yes'"
  TheConfigVersionsNameTheListTest.test_the_grading_version_changes_with_the_image_list
      "does not have the attribute 'IMAGE_FALLBACK_MODELS'"
  TheConfigVersionsNameTheListTest.test_the_reading_version_changes_with_the_image_list
      "does not have the attribute 'IMAGE_FALLBACK_MODELS'"
  TheSecondGraderTest.test_the_default_candidates_are_deepseek_then_haiku
      "'google/gemini-2.5-flash'"
  TheSecondGraderTest.test_no_production_file_names_gemini_2_5_flash
      "'AutoGrader/settings.py'"
ai_processor.tests_grading_config
  test_the_other_values_cover_every_name
      "'IMAGE_FALLBACK_MODELS'"   (Items in the first set but not the second)
  test_the_version_for_a_fixed_reading_is_pinned
      "does not have the attribute 'IMAGE_FALLBACK_MODELS'"
ai_processor.tests_reading_config
  test_each_constant_that_shapes_a_reading_changes_the_version
      "does not have the attribute 'IMAGE_FALLBACK_MODELS'"
ai_processor.tests_grading_hardening
  test_image_fallbacks_never_include_a_nano_model
      "has no attribute 'IMAGE_FALLBACK_MODELS'"
audit.tests_metrics
  test_a_completed_grading_on_the_image_backup_emits_fallback_rate_one
      "call('model_fallback_rate', 0.0)"

GREEN at dac16545 and after (controls):
  EverythingElseIsAsBeforeTest: text call of each page task -> deepseek; list prompt with only text ->
  deepseek; pinned model wins over an image; generate_assignment with an image -> None; pdf_url alone -> deepseek.
  tests_grading_config.test_each_code_constant_changes_the_version (it iterates the code's CODE_CONSTANTS).
  Unchanged on purpose: tests_vote_helper and tests_grading_run_label patch services constants and read the
  config live, so they need no edit (my proposal said they did; that was wrong).
```
