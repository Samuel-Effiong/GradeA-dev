# H-269 notes

Security Engineer (db).

- **The row:** H-269, severity Low, no owner, not scheduled. Mentioned in H-268's notes.
- **Source:** SM 2026-10-27 (move MID_BATCH's "none_finished" from the raiser to the spec).
- **Branch:** `task/h269-mid-batch-variant`, from `3cc85098` (H-268's evidence tip). Batch 30 or 31.
- **Rulings (27 October):**
  1. Move the `variant="none_finished"` choice from the raise site
     (`students/task_tracking.py:534`) to `variant_from_params` on the
     MID_BATCH spec, the way H-268 did for the overage cap.
  2. The client-visible message and error code must stay byte-identical for
     both variants. The only change is who picks the variant.
  3. The raiser's `variant=` still wins (a raiser naming the variant
     explicitly overrides the spec). Since no raiser will name it after
     H-269, this is a safety net test.

## What the read found (at `3cc85098`)

`INSUFFICIENT_CREDITS_MID_BATCH` (`AutoGrader/reason_codes.py:228`) has two messages:
- Default: "Credits ran out after {completed} of {total} items. The finished items are saved."
- `none_finished`: "Credits ran out before any of the {total} items were finished."

The **one build site** is `_mid_batch_error` (`students/task_tracking.py:527`). It currently passes
`variant="none_finished" if completed == 0 else None` (line 534). Both S7c raise sites use it.

The spec has `variant_from_params=None` (set in H-268 as a field, but not used for MID_BATCH).

## What changes

- `AutoGrader/reason_codes.py`: the MID_BATCH spec gains `variant_from_params=_mid_batch_variant`,
  a named function (not a lambda) that returns `"none_finished"` when `completed == 0`, else `None`.
  Named function required because `InsufficientCreditsMidBatchError` stores `self._spec` and the
  pickle serialization tests (Celery result backend) iterate over all CodedError subclasses —
  a lambda on the spec makes the error unpicklable. Gate stop on 53216208 caught this.
- `students/task_tracking.py:534`: remove the `variant=` argument. The raiser no longer picks the
  variant; the spec picks it from the same `completed` param.
- `AutoGrader/tests_reason_codes.py`:
  - The H-268 control (`test_control_the_caller_named_variant_is_untouched`) is turned around:
    the spec now HAS `variant_from_params`, and the args are a 4-tuple (no variant in args).
    Renamed to `test_the_spec_picks_none_finished_from_completed`.
  - New: `test_mid_batch_with_items_finished_the_spec_picks_the_default` (completed > 0 → None).
  - New: `test_mid_batch_a_raiser_that_names_the_variant_still_wins` (safety net).
  - New: `test_mid_batch_args_keep_the_4_tuple` (spec-chosen, not in args).
  - `MessageVariantTests.test_the_variant_renders_its_own_template`: the
    `variant="none_finished"` case now also works without naming the variant
    (completed=0 triggers the spec). Both paths are tested.
- `students/tests_s7d_mid_batch_wording.py`: no change needed — it calls
  `_mid_batch_error`, which will stop naming the variant, and the spec picks
  it. The messages are byte-identical.

## Not changed here

- The OVERAGE_CAP_EXCEEDED spec (H-268): unchanged.
- The raise sites (`students/task_tracking.py:527`): still calls `InsufficientCreditsMidBatchError`,
  but no longer passes `variant=`.

## Tests

### tests_reason_codes.py: SpecChosenVariantMidBatchTests (new class, 5 tests)

- `test_the_spec_picks_none_finished_from_completed`: completed=0 → spec picks "none_finished";
  renders the correct sentence; spec.variant_from_params is not None.
- `test_with_items_finished_the_spec_picks_the_default`: completed=2 → spec picks None;
  renders the default sentence.
- `test_boundary_one_finished_is_the_default`: completed=1 → the default sentence (the boundary;
  kills mutant M3 which changes == 0 to <= 1).
- `test_a_raiser_that_names_the_variant_still_wins`: a raiser naming
  `variant="none_finished"` on completed=2 overrides the spec (which would return None).
- `test_args_keep_the_4_tuple`: at completed=0, the spec-chosen variant is NOT in args;
  rebuilt from args it still renders the same sentence.

### Existing tests updated

- `SpecChosenVariantTests.test_control_the_caller_named_variant_is_untouched`: turned around to
  assert the spec HAS `variant_from_params`. The old assertion (spec.variant_from_params is None)
  is now the red test.

### tests_s7d_mid_batch_wording.py (existing, 2 tests)

- `test_nothing_finished_reads_before_any`: unchanged, still green — the message is byte-identical.
  Calls `_mid_batch_error` end-to-end with completed=0, asserts the "before any" sentence.
- `test_once_one_finished_it_reads_after_n_of_m`: unchanged, still green.
  Calls `_mid_batch_error` end-to-end with completed=1 (one item SUCCESS), asserts the "after 1 of 4" sentence.

Both tests drive `_mid_batch_error` through the S7c raise site path and assert the wording.
Both are in the near list (`students.tests_s7d_mid_batch_wording`). SM condition met.

## Expected reds against `3cc85098` (written before the gate)

At the base, MID_BATCH has `variant_from_params=None` and the raiser passes
`variant="none_finished" if completed == 0 else None`. The old control test
(`test_control_the_caller_named_variant_is_untouched`) is removed and replaced.

3 blocks (3 FAIL, 0 ERROR), "Ran 5 tests". Two tests are green at the base.

1. `test_the_spec_picks_none_finished_from_completed`: FAIL — `assertIsNotNone(None)` because
   `spec.variant_from_params` is None at the base. ~~Fragment: "is not None".~~
   Corrected per SM ruling on gate stop at 69d3452f: the assertIsNotNone failure message is
   "unexpectedly None", not "is not None". Fragment: both "assertIsNotNone(spec.variant_from_params)"
   AND "unexpectedly None" in the block.
2. `test_with_items_finished_the_spec_picks_the_default`: FAIL — `assertIsNotNone(None)` for
   the same reason. ~~Fragment: "is not None".~~
   Corrected per same ruling. Fragment: both "assertIsNotNone(spec.variant_from_params)"
   AND "unexpectedly None" in the block.
3. `test_args_keep_the_4_tuple`: FAIL — at the base, `CodedError(MID_BATCH, completed=0)` without
   `variant=` renders the default sentence ("after 0 of 4"), not the none_finished sentence.
   `assertEqual` mismatch. Fragment: "!=".

Green at the base:
- `test_a_raiser_that_names_the_variant_still_wins`: the raiser's `variant="none_finished"` on
  completed=2 works identically at the base (no spec to interfere, and the raiser's variant wins).
- `test_boundary_one_finished_is_the_default`: completed=1 without variant gets the default
  sentence at the base too (the spec doesn't interfere). This is the mutant M3's kill test.

## Expected mutant results (written before the gate)

3 mutants, all on `AutoGrader/reason_codes.py` (the MID_BATCH spec's `variant_from_params`).
Expected: 3/3 KILLED, 0 SURVIVED, 0 BROKEN.

| ID | What | Expected status | Expected failing tests |
|----|------|-----------------|------------------------|
| M1 | drop variant_from_params (None) | KILLED | spec_picks ("is not None"), default ("is not None"), args ("!=") |
| M2 | always return "none_finished" | KILLED | default ("!="), boundary ("!=") |
| M3 | boundary: completed <= 1 | KILLED | boundary ("!=") |

Security Engineer (db)
