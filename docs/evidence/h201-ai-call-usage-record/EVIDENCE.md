# H-201 per-call AI usage record: evidence

One row per AI call in `ai_processor_aicallrecord` (numbers and ids only), written by
`ai_processor/call_record.py` after the call and its charge are finished. One additive
migration (`0006_aicallrecord`); switch `AI_CALL_RECORD_ENABLED` (default on). Builder a0;
pre-read, GRANT and verification by the Senior Manager.

## Base update onto beta 3d5174b0

- `a5c12e68`: merge of beta 3d5174b0, no conflicts. `execute_graded_task` is identical on both
  sides (by AST), so the five hook lines are where they were; `0006` still follows `0005`.
- `ed6d6a02`: `CALL_SITES` names beta's 17th `execute_graded_task` site, `_second_reading`
  (H-253, `extract_answer`, no kind); gate script on the new base (memory cap from available
  memory, watchdogs, mutant time limit 3660 s, seven added modules).

## Gate on ed6d6a02: STOPPED RED (kept on the record)

`stopped_ed6d6a02/` (gzipped). Steps 0, 1a and 1b were as written; step 1 stopped red at
"Ran 781 tests, FAILED (failures=3, errors=5)", all 8 in `tests_call_record`, which had never
been run before. All 8 were test-fixture faults, fixed in `49698d4c` (tests only): a second
`_make_teacher_with_credits()` re-made the unique PRO plan (2 tests); `_classroom`'s teacher has
credits but no subscription (3 tests); the charge test compared with the last pass's reply;
`assertRaises` drops the traceback the traceback test reads; the no-new-lock check matched SQL
text that carries each call's time.

## Gate on 49698d4c: GREEN

`gate_49698d4c/` (gzipped), 11:03:10 to 11:12:12 WAT 24 October 2026, load 3.72 to 4.02, cap 8G.

| Step | Result |
| --- | --- |
| 0 red run (the two modules against beta's code) | exit 1, "Ran 2 tests", "FAILED (errors=2)"; both modules red for the written reason `cannot import name 'call_record' from 'ai_processor'` |
| 1a makemigrations --check | exit 0, "No changes detected" |
| 1b the seam | the function without its hook lines is identical to beta; 4 notes + the decorator; both `__ai_model` calls unchanged |
| 1 own + near modules + guards | "Ran 781 tests in 177.904s", "OK" |
| 2 mutants | 34/34 KILLED, none survived or broken; each with its own Ran line |

Recorded, not hidden: Q4 (a second write inside the charge transaction) also failed
`test_deleting_the_teacher_or_the_assignment_leaves_the_rows`, outside its expected set.

## After the gate

`dddd7cec`: the traceback test fails through `self.fail` in a `try/except/else` instead of a
bare `assert` (works under `python -O`; `assertIsNotNone` was tried first and mypy does not
narrow on it). Test-only, no new run (Senior Manager's ruling).
