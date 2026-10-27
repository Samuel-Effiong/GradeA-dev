# H-268 notes

Security Engineer (db).

- **The row:** new in this pack, severity Low (user-facing wording, the owner's).
- **Source:** SM 2026-10-27 (the owner's wording for OVERAGE_CAP_EXCEEDED).
- **Branch:** `task/h268-overage-cap-wording`, from beta `fef4249d`. Batch 30.
- **Rulings (27 October):**
  1. Two sentences, picked from the params by the spec — not at the raise site.
  2. The grant-time re-check (Stripe webhook) has no client sentence; leave it, cover it as a control.
  3. `MID_BATCH_CREDIT_EXHAUSTION`'s caller-named "none_finished" behaves exactly as today; pinned, not moved.
     That earns its own row, H-269 (Low, no owner, not scheduled).
  4. Message and `field_errors.error` stay identical.

## What the read found (at `fef4249d`)

`OVERAGE_CAP_EXCEEDED` (`AutoGrader/reason_codes.py:272`) was a single `ReasonSpec` message:

> "This purchase would go over your plan's limit of {max_blocks} block(s) per billing cycle. You have {remaining}
> block(s) left this cycle."

H-233 fixed the worst case ("Reduce your quantity to 0 or fewer"), but the sentence still reads "You have 0 block(s)
left this cycle" when the cap is fully used.

The **one raise site** is `create_overage_checkout_session` (`billing/stripe_service.py:2299`): it passes `params` only
(`max_blocks`, `remaining`), never a variant. No other code raises `OVERAGE_CAP_EXCEEDED`.

The **grant-time re-check** (`billing/stripe_service.py:3134`, `_handle_overage_checkout_completed`): answers Stripe's
webhook. If `(used + quantity) > max_overage_blocks` it logs an ERROR, records a paid-but-not-granted
`BillingTransaction` for manual refund, and returns. It raises nothing, builds no envelope, and answers no teacher.
`docs/GRADE_A_PLUS_REASON_CODES.md` already said so: "that path has no reason code, since it answers Stripe's webhook,
not the teacher." The SM confirmed: "leave the grant-time path untouched."

## What changes

- `AutoGrader/reason_codes.py`:
  - `ReasonSpec` gains `variant_from_params: Callable | None` (default `None`): picks a key of
    `alternative_messages` from the params when the raiser names no variant.
  - `CodedError.__init__`: after the raiser's `variant` is validated and before the placeholders/display checks, if
    `variant is None` and `spec.variant_from_params is not None`, `chosen = spec.variant_from_params(params)`.
    The raiser's own `variant=` still wins, and the spec-chosen one does NOT ride in `self.args` (it is re-derived
    from the same params), so every existing error keeps its 4-tuple.
  - `OVERAGE_CAP_EXCEEDED`'s message is now the owner's first sentence; `alternative_messages` gains `"cap_reached"`
    with the zero sentence; `variant_from_params` returns `"cap_reached"` when `remaining == 0`, else `None`.
- `docs/GRADE_A_PLUS_REASON_CODES.md`: the entry now shows both sentences and when each is used.
- **Visible change:** with no blocks left the teacher reads "Purchase limit reached — you have used all {max_blocks}
  blocks for this billing cycle. You can buy more when your next cycle starts." instead of "You have 0 block(s) left
  this cycle."

## Not changed here

- The grant-time re-check: no client sentence to share, and the SM ruled it untouched. Covered as a control.
- The raise site (`billing/stripe_service.py:2299`): still passes only `params`, never a `variant`.
- `MID_BATCH_CREDIT_EXHAUSTION`'s "none_finished": still chosen at the raise site (`students/task_tracking.py:534`).
  H-269 may move it to `variant_from_params`; H-268 does not.

## Tests

### tests_reason_codes.py: SpecChosenVariantTests (7 tests)

- `test_with_blocks_left_the_sentence_names_what_is_left`: remaining > 0 → the first sentence.
- `test_with_none_left_the_sentence_sends_them_to_the_next_cycle`: remaining == 0 → the second sentence; "or fewer"
  not present (H-233).
- `test_both_params_reach_the_body_whichever_sentence_shows`: coded_response envelope has both params for both
  sentences.
- `test_the_spec_chose_it_so_the_args_keep_their_shape`: the spec-chosen variant is NOT in args (4-tuple); rebuilt
  from args it still renders the same sentence.
- `test_a_raiser_that_names_the_variant_still_wins`: `variant="cap_reached"` on remaining=1 overrides the spec.
- `test_control_the_caller_named_variant_is_untouched`: MID_BATCH has no `variant_from_params`; "none_finished" still
  renders its sentence and rides in args[5].

### test_overage_cap.py: updated and extended (15 tests total)

- Template updated to the owner's sentences (`CAP_SENTENCE`, `CAP_REACHED_SENTENCE`).
- `test_cap_already_reached_rejects_even_a_single_block`: now asserts `CAP_REACHED_SENTENCE` and "or fewer" absent.
- `test_the_rendered_body_carries_the_sentence_in_both_places`: message and field_errors.error byte-identical, U+2014
  present (remaining > 0).
- `test_at_zero_left_both_places_read_the_other_sentence`: same for remaining == 0; both params in the envelope.
- `test_control_the_grant_time_recheck_writes_neither_sentence`: the webhook path grants nothing, records the
  transaction, and neither sentence's distinctive fragments appear in its log or description.

## Expected reds against `fef4249d` (written before the gate)

10 blocks (8 FAIL, 2 ERROR), "Ran 15 tests". The 5 controls are green (grant-time × 3, within_cap, both_params).

The tests that are red against beta:
- `test_with_blocks_left_the_sentence_names_what_is_left`: FAIL (new sentence vs old).
- `test_with_none_left_the_sentence_sends_them_to_the_next_cycle`: FAIL (new sentence vs old).
- `test_the_spec_chose_it_so_the_args_keep_their_shape`: FAIL (rebuilt from args renders old sentence, not REACHED).
- `test_a_raiser_that_names_the_variant_still_wins`: ERROR (no "cap_reached" variant on old spec).
- `test_control_the_caller_named_variant_is_untouched`: ERROR (no `variant_from_params` attribute).
- `test_cap_already_reached_rejects_even_a_single_block`: FAIL (asserts new sentence).
- `test_quantity_exceeding_remaining_cap_is_rejected_before_stripe`: FAIL (asserts new sentence).
- `test_exceeding_the_cap_answers_the_coded_envelope`: FAIL (asserts new sentence in envelope).
- `test_the_rendered_body_carries_the_sentence_in_both_places`: FAIL (asserts new sentence).
- `test_at_zero_left_both_places_read_the_other_sentence`: FAIL (the zero sentence doesn't exist).

Green at the base: grant-time (3 tests: the existing 2 + the new control), within_cap, both_params.

## Gate on `681e30f0`

Run 12:36:56–12:41:18, load1 2.64→6.10, 10G cap.

### 0. Red run (own DB)
10 blocks (8 FAIL, 2 ERROR), Ran 15 in 0.755s. Each block holds its written fragment. Modules loaded.

### 1a. makemigrations --check
Exit 0, no changes.

### 1b. Seam by program
`AutoGrader/reason_codes.py` changed only: `from collections.abc import Callable`, `ReasonSpec`,
`REASON_CODES`, `CodedError.__init__`. `__init__` has `chosen = variant` / `variant_from_params` consultation;
`self._variant` set from `variant`, not `chosen`. SEAM OK.

### 1. Own + near + guards
Ran 682 in 175.992s, OK.

### 2. Mutants (5, own DB)

| ID | What | Status | Expected | Also failed |
|----|------|--------|----------|-------------|
| M1 | swap the two sentences | KILLED | 8 tests | — |
| M2 | drop the zero branch | KILLED | 4 tests | — |
| M3 | boundary remaining ≤ 1 | KILLED | 4 tests | — |
| M4 | spec overrides raiser | KILLED | 1 test | — |
| M5 | spec-chosen in args | KILLED | 1 test | — |

5/5 KILLED, 0 SURVIVED, 0 BROKEN. No key-shaped text in any log. Exit 0.

## H-268 re-gate: lambda to named function (pickle fix)

SM ruling 2026-10-27: the lambda in `variant_from_params` makes CodedError unpicklable when
the error stores `self._spec`. `InsufficientCreditsMidBatchError` (a subclass) was caught by
the serialization tests; plain `CodedError(OVERAGE_CAP_EXCEEDED)` was not tested by the subclass
iteration but has the same defect. H-268 introduced this; no lambda existed before.

### What changes (re-gate)

- `AutoGrader/reason_codes.py`: lambda replaced by named module-level function `_overage_cap_variant`.
- `AutoGrader/tests_reason_codes.py`: new test
  `test_every_spec_with_variant_from_params_survives_pickle` in SpecChosenVariantTests: builds and
  pickle-round-trips a plain `CodedError` for every ReasonCode whose spec has `variant_from_params`.

### Expected reds against `3cc85098` (re-gate, written before the run)

1 block (0 FAIL, 1 ERROR), "Ran 1 test". The error is for OVERAGE_CAP_EXCEEDED: PicklingError
because the lambda can't be pickled. Fragment: "PicklingError".

### Expected mutant results (re-gate, M1–M5 re-run)

The mutant anchors change: the lambda text is now `_overage_cap_variant` (the function reference)
and the function body. All 5 expected KILLED as before.

| ID | What | Expected | Notes |
|----|------|----------|-------|
| M1 | swap the two sentences | KILLED | function unchanged, sentences swapped |
| M2 | drop the zero branch | KILLED | function body changed |
| M3 | boundary remaining ≤ 1 | KILLED | function body changed |
| M4 | spec overrides raiser | KILLED | CodedError.__init__ unchanged |
| M5 | spec-chosen in args | KILLED | CodedError.__init__ unchanged |

Security Engineer (db)
