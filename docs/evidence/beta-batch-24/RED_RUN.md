# Batch 24: the first full run, red (kept on the record)

Batch 24 was b02de3fb + H-254 (6bbdce5f, merge ad791b66) + H-215a (9ef2f18e, merge c666408a). The Senior Manager
granted one strict full run on c666408a at 09:47 WAT on 25 October 2026. It went red on one test. This file says
why, and why batch 24 has a second full run.

## The run

- Script: `strict_bundle.sh` (sha256 756dc7e3). It is batch 23's script with only the output folder changed; a copy
  is in `red_c666408a/strict_bundle.sh.txt`.
- Pre-start check 09:47:25: branch task/beta-batch-24 at c666408a, tree clean, test database `test_beta_batch_24`,
  load 2.32, 13,418 MB available, no other app test run.
- The script's pre-checks passed: mypy, then makemigrations ("No changes detected"). Cap 10G (13,151 MB available
  at start). Start 09:47:51, load 2.03 6.10 6.53.
- End 09:59:40, wall 709 s, exit 1. **Ran 8311 tests in 670.714s, FAILED (failures=1, skipped=30).** FAIL/ERROR
  headers counted in the full log: 1 FAIL, 0 ERROR.
- Stall watchdog: did not fire. Memory guard: did not fire (lowest available 10,011 MB). Suspends: 0.
  blocked_outbound=8. Load at end 8.94 9.52 8.28.
- Logs: `red_c666408a/strict_b24.log.xz` (the full log) and `red_c666408a/strict_b24.summary.txt`.

## The failure

`ai_processor.tests_benchmark_golden.BenchmarkGoldenMasterTest.test_replay_report_matches_committed_snapshot`:

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

## Cause: H-254, not H-215a

- The benchmark replays recorded model answers. Each answer is found by a key made of the system prompt, the user
  prompt and the second grader's model (`request_key`, `ai_processor/benchmark/runner.py:122-128`).
- The recordings hold 127 second-grader answers, all from `deepseek/deepseek-v4-pro`, and none from haiku.
- The benchmark's pinned settings did not pin `GRADING_SECOND_OPINION_MODELS`. After H-254 the second grader is
  haiku, so every second opinion found no recording and raised `MissingRecordingError`. The second opinion's broad
  error handler caught it: 0 ran, 21 errors.
- The token drop is the DeepSeek second-grader answers that were no longer replayed.
- H-254's gate did not include `tests_benchmark_golden`, so the gate could not see this.

## The ruling and the fix (Senior Manager, option (a))

- The benchmark pins the second grader's candidates to the order the recordings were made under (deepseek, then
  haiku). Production keeps haiku first.
- The fix is an H-254 follow-up commit (de456dd7). Its notes, its gate pack (09dac130) and its gate are in
  `docs/evidence/h254-second-opinion-haiku-first/`.
- Regenerating the snapshot was refused: the new numbers record second opinions that failed.
- Re-recording with haiku as the second grader means live, paid calls. The Senior Manager will put it to the owner
  as a later option.
- The new H-254 tip is merged into task/beta-batch-24, and the batch gets one new full run. Its results are in
  `FULL_SUITE.md`.
- Lesson: any change to a model list or to the second opinion's settings puts `ai_processor.tests_benchmark_golden`
  and `ai_processor.tests_grading_benchmark` in its gate.
