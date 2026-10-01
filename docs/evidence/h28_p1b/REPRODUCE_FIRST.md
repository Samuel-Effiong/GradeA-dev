# H-28 Change 1 — reproduce-first evidence (Gate 1, H2.1)

**Product code at `b744c9f`, unmodified.** The commit carrying this file adds only the test
module `billing/tests/test_h28_licence_stripe_divergence.py` and documents; each log opens
with `git diff --stat b744c9f -- . ':!docs' ':!<test module>'`, which is empty.

Run as a targeted module run (0 slots) on the session-unique DB `test_p1b_divergence`, fresh,
no `--keepdb`.

| Log | Tests | Result | Lines | sha256 |
|---|---|---|---|---|
| `reproduce_first_b744c9f_run1.log` | 14 | 14 FAIL | 513 | `7e6a4aa1fe52df45ca178c67b0d17833b8feff902511e5e18b28deb1b8aa5a44` |
| `reproduce_first_b744c9f_run2.log` | 18 | 18 FAIL | 578 | `5e0a4978948a72c2c830fad7b3515adf7ef0a93cf3c98f68f42fc706f770e57f` |
| **`reproduce_first_b744c9f_run3.log`** | 18 | **18 FAIL — CANONICAL** | 579 | `c92facc1f93d8f5a0dc2ee83c2ca8ee0619869757c64cc3db6ed79c1c6e2e4fa` |

Run 1 is kept because it is what **exposed F0**: all five `change_license_plan` scenarios
failed at their precondition (Stripe was never called), which led to finding why.

**Run 3 is canonical, and why it exists.** The first commit attempt was stopped by two
pre-commit hooks: `black` reformatted the test module, and `trailing-whitespace` edited
run 2's log — which would have made the checksum recorded for it false. So:
1. **Run 3 was executed on the exact black-formatted test module being committed** (its
   sha256 is in the log header). Its per-test failure classification is **identical** to run
   2's (13 invariant, 5 precondition), checked by script, not by eye.
2. **Trailing whitespace was stripped from all three logs by me, before hashing** — the
   repo's pre-commit rule would otherwise have done it after. That is the only edit made to
   any log; content is otherwise unfiltered. The hashes above are of the committed files.

## Every failure, classified by its REASON

A failure only counts as a reproduction if it fails **for the named defect**. Each was
checked against its assertion message, not just its FAIL status.

| Test | Fails on | Reproduces |
|---|---|---|
| `test_F0_plan_change_with_no_failure_updates_the_stripe_price` | invariant: Stripe 12000 vs app 24000 | **F0** |
| `test_cancel_killed_mid_stripe_call_…` | invariant: renewal disagrees | P1 cancel, 60 s kill |
| `test_seat_decrease_killed_mid_stripe_call_…` | invariant: quantity 7 vs 10 | P1 update_seats, 60 s kill |
| `test_convert_to_offline_killed_mid_delete_…` | invariant: deleted but still STRIPE | **P0**, 60 s kill |
| `test_F4_seat_increase_declined_…` | invariant: open invoice left | F4 |
| `test_F4_seat_increase_needing_3d_secure_…` | invariant: open invoice left | F4 (licence-path `requires_action`, required by d4) |
| `test_F5_seat_increase_card_error_…` | invariant: quantity 15 vs 10 | F5 |
| `test_latent_F1_price_change_needing_3d_secure_…` | invariant: Stripe 24000 vs app 12000 | F1 (latent) |
| `test_latent_F2_price_change_card_error_…` | invariant: Stripe 24000 vs app 12000 | F2 (latent) |
| `test_latent_F3_price_change_declined_…` | invariant: open invoice left | F3 (latent) |
| `test_cancel_applied_but_response_lost_…` | invariant: renewal disagrees | unknown-outcome rule |
| `test_seat_decrease_applied_but_response_lost_…` | invariant: quantity 7 vs 10 | unknown-outcome rule |
| `test_convert_to_offline_delete_applied_but_response_lost` | invariant: deleted but still STRIPE | **P0 misreport** — the named test 95 asked for |
| `test_F1_plan_upgrade_needing_3d_secure_…` | **precondition** — Stripe never called | **F0, not F1** |
| `test_F2_plan_upgrade_card_error_…` | **precondition** | **F0, not F2** |
| `test_F3_plan_upgrade_declined_…` | **precondition** | **F0, not F3** |
| `test_plan_downgrade_killed_mid_stripe_call_…` | **precondition** | **F0, not the kill** |
| `test_plan_downgrade_applied_but_response_lost_…` | **precondition** | **F0, not the unknown outcome** |

**Stated plainly:** 13 tests reproduce their named defect. **5 do not** — on `b744c9f` their
target code is unreachable through `change_license_plan` because of F0, so they reproduce F0
instead. They stay in the suite because they are the correct regression tests once F0 is
fixed, and the precondition guard means they cannot pass vacuously. The defects they name
(F1-F3 and the downgrade kill/timeout) are reproduced by the `latent_*` tests, which call
`change_license_price` directly.

## Test design notes, for the H11.1 read

- **One whole-record invariant**, `_assert_stripe_and_app_agree`: price, seat quantity,
  renewal, billing method and open invoices, every time — not only the field a scenario
  touches. A fix that repairs one field while breaking another fails here.
- **A precondition guard** (`_assert_stripe_was_mutated`) on every scenario, so a test cannot
  pass vacuously because the code stopped calling Stripe.
- **The 60 s kill is modelled, not assumed:** `_IdleInTransactionKill` fires only if a
  transaction was open when Stripe was called — Postgres' actual condition. Code that calls
  Stripe outside a transaction is untouched by it, which is the property the fix must earn.
- **Real `StripeObject`s**, not dicts: production reads them both ways (`price.id`,
  `sub["items"]`).
- **Fixtures as production writes them:** `contract_months=12` (the model default; the help
  text lists 9/10/12), so `change_license_price` takes its real `Price.create` path rather
  than a `contract_months=1` shortcut production never uses.
- **These are MOCKED-Stripe tests (Gate 1).** They are not Gate 7 evidence. Real Stripe test
  mode — including verifying whether Stripe ignores idempotency keys on DELETE — comes later.
- **Not a competing harness:** e2's `testing_fake_stripe.py` records `in_transaction` for the
  Gate-5 runtime proof and is not merged here (it has not passed its own gate). This module
  reads `connection.in_atomic_block` only to decide whether the modelled Postgres kill fires.
