# H-254 notes

- **What changed** (the owner's decision, 24 October 2026): `GRADING_SECOND_OPINION_MODELS` defaults to
  `["anthropic/claude-haiku-5.5", "deepseek/deepseek-v4-pro"]`, haiku first. `AutoGrader/settings.py` is the only
  production file. Its one code change is the default line; the comment above it is rewritten.
- **Why:** a second-opinion request carries the images of the questions it re-grades
  (`_grade_question_batch` adds `_question_image_content_blocks` for an http(s) `question_image`), and deepseek
  cannot read images. With deepseek first, grader B's call failed on every such paper. The broad `except` after
  the second opinion then let grader A's grade stand with no second opinion and no review flag. Haiku reads
  images and is cheaper than deepseek in list price ($0.10/$0.50 against $0.338/$0.675 per million tokens).
- **On the normal path** (grader A = grok-4.3), grader B is now haiku, with images or without.
- **Stated limit, not changed:** when grader A itself fell back to haiku (an image call while grok is down),
  grader B is deepseek, which still fails on the images. That paper keeps grader A's grade without a second
  opinion, as before H-254. A control test pins it
  (`test_when_grader_a_fell_back_to_haiku_grader_b_is_deepseek`).
- **Effect after deploy:** `GRADING_SECOND_OPINION_MODELS` is one of the settings in the grading config version,
  so the version changes and saved grading answers are not reused for one round (as after H-205). No test pins
  the real version: `tests_grading_config` pins a version built from its own constants.
- **An H-205 test changed:** `test_the_default_candidates_are_deepseek_then_haiku` became
  `test_the_default_candidates_are_deepseek_and_haiku` (`assertCountEqual`: the two names in any order). The new
  module owns the order.
- **The list is read from settings.py's syntax tree** (`_settings_default`, from the H-205 module), so the tests
  do not depend on the environment of the run.
- **Mutant S4 (B unpinned)** breaks grader B's call site in `_maybe_run_second_opinion` (`override_model=None`).
  The `execute_graded_task` override block was H-205's I4. The mutant battery is the two modules only;
  `tests_second_opinion_pipeline` is in step 1.

## Expected reds against b02de3fb (written before any run)

```
ai_processor.tests_h254_second_opinion_haiku_first
  TheDefaultOrderTest.test_the_default_candidates_are_haiku_then_deepseek
      "['deepseek/deepseek-v4-pro', 'anthropic/claude-haiku-5.5'] != ['anthropic/claude-haiku-5.5', 'deepseek/deepseek-v4-pro']"
  TheChoiceFromTheDefaultTest.test_grader_a_on_grok_gives_haiku
      "'deepseek/deepseek-v4-pro' != 'anthropic/claude-haiku-5.5'"
  TheRealSecondOpinionPathTest.test_a_question_with_an_image_is_re_graded_by_haiku
      "'deepseek/deepseek-v4-pro' != 'anthropic/claude-haiku-5.5'"
  TheRealSecondOpinionPathTest.test_a_question_without_an_image_is_re_graded_by_haiku_too
      "'deepseek/deepseek-v4-pro' != 'anthropic/claude-haiku-5.5'"
GREEN at b02de3fb and after (controls): test_grader_a_on_haiku_gives_deepseek,
  test_grader_a_on_deepseek_gives_haiku, test_when_grader_a_fell_back_to_haiku_grader_b_is_deepseek,
  and every test of tests_h205_image_fallback. The script also checks that exactly four FAIL/ERROR blocks appear.
```

## The gate on 92854ca9 (granted by the Senior Manager; accepted)

- Started 2026-10-24 17:30:24, after waiting for load1 under 8 (12.46 at 17:28:56; 6.06 at the pre-start check
  at 17:30:17). 11,167 MB available, so a cap of 8G. Exit 0 at 17:35:36.
- **Step 0:** Ran 25, FAILED (failures=4), each red for its written fragment and no other failure.
- **1a:** makemigrations, no changes. **1b:** the seam passed.
- **Step 1:** own + 22 near modules + 21 guards (rule 20's `tests_cache_bespoke_1114` included): Ran 788 tests in
  197 s, OK.
- **Step 2:** S1-S4 KILLED, each with its Ran line, every expected test failing and nothing else.
- No memory or stall watchdog action, no blocked outbound. Load at the end: 3.80 7.26 7.25.
- Logs: `gate_92854ca9/` (gzipped).
