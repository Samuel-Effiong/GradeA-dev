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

## Follow-up after the red batch 24 full run (the Senior Manager's ruling (a), 25 October 2026)

- **What the full run found:** batch 24 (c666408a = b02de3fb + H-254 + H-215a) ran 09:47:51-09:59:40: Ran 8311,
  FAILED (failures=1, skipped=30). The one failure is `ai_processor.tests_benchmark_golden`
  `test_replay_report_matches_committed_snapshot`. This is the written red, quoted from that run's log:

  ```
  AssertionError: Lists differ: ['  cost.total_tokens: snapshot=920349 now[349 chars]one'] != []
  -  '  cost.total_tokens: snapshot=920349 now=463926',
  -  '  second_opinion.coverage.error: snapshot=1 now=21',
  -  '  second_opinion.coverage.ran: snapshot=20 now=0',
  -  '  second_opinion.disagreement_rate: snapshot=0.1441 now=None',
  -  '  second_opinion.questions_compared: snapshot=111 now=0',
  -  '  second_opinion.questions_disagreed: snapshot=16 now=0',
  -  '  second_opinion.tiers.moderate: snapshot=16 now=None'] : The benchmark's replay results changed.
  ```

- **Cause:** the replay key is the system prompt, the user prompt and `override_model` (`request_key`,
  `benchmark/runner.py:122-128`). The recordings (`benchmark/recordings/responses.json.gz`) hold 203 calls with no
  override and 127 with `deepseek/deepseek-v4-pro`, and none with haiku. `_benchmark_settings` did not pin
  `GRADING_SECOND_OPINION_MODELS`, so after H-254 grader B was haiku. Every second opinion raised
  `MissingRecordingError`, which the second opinion's broad `except` catches. The token drop is the DeepSeek
  grader B answers no longer replayed.
- **The fix:** `_benchmark_settings` pins `GRADING_SECOND_OPINION_MODELS` to `[deepseek, haiku]`, the order the
  recordings were made under. Its docstring says so, and says re-recording is the way to change it. Production
  keeps haiku first. The pin also covers the nightly replay task (`ai_processor/tasks.py`
  `nightly_grading_benchmark_replay`), which runs the same `execute_benchmark`. Re-recording with haiku as
  grader B (live, paid) is a later option for the owner, put to them by the Senior Manager.
- **Every other model list a replay could read** (grep of the benchmark and its tests):
  - `MAIN_MODEL`, `GRADING_FALLBACK_MODELS`, `IMAGE_FALLBACK_MODELS`, `DEFAULT_FALLBACK_MODELS`: not in the replay
    key (they are the request's `model` and `models`, not `override_model`), so a change cannot miss a recording.
    They are module constants, which `override_settings` could not pin anyway. Not pinned, and need not be.
  - `ANSWER_BLANK_VERIFICATION_MODEL`: does reach `override_model` (`services.py:2437`), but only when answers are
    read. The grading benchmark grades `submission_answers_json` directly, and the extraction benchmark reads
    assignments, not answers, so neither reaches it. Not pinned; green today for that reason.
  - The extraction benchmark (`extraction_runner.py`) pins nothing (`_extraction_settings` is `{}`) and makes no
    override call. `benchmark/answers/live.py` and `isolation_run8` run live only, with no replay key.
  - The only list that needed a pin is the second opinion's.
- **Lesson:** H-254's gate did not run `tests_benchmark_golden`. Any change to a model list or to the second
  opinion's settings puts `ai_processor.tests_benchmark_golden` and `ai_processor.tests_grading_benchmark` in its
  near modules.
