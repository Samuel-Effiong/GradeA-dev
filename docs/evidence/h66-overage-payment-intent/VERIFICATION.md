# Verification: H-66, a paid overage session without a payment_intent is refused @ cf7df763

**Verifier:** 1a. **Author:** d5. **Date:** 2026-10-01.
**Branch:** `task/h66-overage-no-payment-intent` @ **cf7df763**:
- `552a3bf8`: the tests
- `3f2295cb`: the fix
- `c23e71f4`: the base update onto bundle 4's final tip `67a0681`
- `a1832485`: logs
- `58d5efb3`: my Q1 adopted as a test, plus mutant K4
- `cf7df763`: the evidence

The evidence is in `docs/evidence/h66-overage-payment-intent/`.

I ran in 0b's slot from my detached scratch checkout at cf7df763, with its own test DB (`test_vf_h66`, mutant `test_vf_h66_mut`). The wrapper was rule 16's `systemd-inhibit`, the 6G scope with `MemorySwapMax=0`, `nice -n 10` and `timeout -k 60 1800`. Under rule 15 I cite d5's regression (billing + 9 guards, 2036 OK at a1832485) and don't repeat it.

**Verdict: VERIFIED.**
- The refusal is correct, comes in the stated order, and logs ids only.
- The one gap my pre-review found was the order against the cap check. It is closed by `58d5efb3` and proven by my mutant Y1, as the SM ruled.

## Static review
**The fix** (`billing/stripe_service.py:3021–3039`, `_handle_overage_checkout_completed`):
- It refuses a missing **or empty** `payment_intent` with an ERROR. The checks run in this order:
  1. the duplicate-delivery check, which runs only with a key;
  2. the unpaid refusal;
  3. **the H-66 refusal**;
  4. the cap check.
- It returns before any grant, `BillingTransaction` or receipt.
- **The log line** carries ids only: the session id, the wallet id and `payment_status`.

**Reachability:**
- Two callers reach the handler: the webhook (`webhooks.py:103` → `handle_checkout_completed` → `:2825`) and the failed-event replay (`event_replay.py:86`). Both pass Stripe's payload, where `payment_intent` is a string id.
- Nothing retrieves or expands a Checkout Session.
- The unpaid check already refuses `no_payment_required`, so the new refusal is reached only with `payment_status` "paid" or missing. d5's test covers the missing case.

**Tests:**
- Every other overage session builder defaults to a key: `test_overage_purchase_integrity`, `test_overage_refund_lifecycle`, `test_receipt_lookup_outside_transaction` and `test_event_replay`. The one keyless fixture, `test_overage_never_expires`, is fixed in `3f2295cb`.
- **Rule 14:** there is no MagicMock in the new or changed tests.

**The base update** `c23e71f4` adds only H-66's own files over 67a0681. Production code is unchanged after `3f2295cb`: `git diff c23e71f4 cf7df763 -- billing/stripe_service.py` is empty.

**The mutation runner's wrapping** (the SM asked me to check it):
- `run_mutants.py` starts `manage.py` as a subprocess with no wrappers of its own.
- d5 invoked the whole runner inside `systemd-inhibit … systemd-run --scope 6G … timeout -k 60 1800`, so every child runs inside that one scope and timeout.
- EVIDENCE.md says so. Accepted.

## The order gap (pre-review → the SM's ruling → closed)
- **The gap:** d5's K1–K3 didn't pin "before the cap check". My mutant **Y1** (`h66_mutant_Y1.py`) physically moves the whole H-66 block below the cap block.
- **What Y1 does to the behaviour:** a keyless paid session for a wallet already at `max_overage_blocks` takes the cap path. It records a **PAID `BillingTransaction` with `stripe_payment_intent_id=None`** and logs the cap ERROR instead of the H-66 one.
- **The SM's ruling:** a money-path ordering defect waiting to happen; adopt Q1 as a test before the batch merge.
- **The fix:** `58d5efb3` adds `test_a_keyless_session_at_the_cap_is_refused_as_keyless_not_capped`, my Q1 essentially verbatim.

## Evidence
| Check | Result |
|---|---|
| **Run** @ cf7df763: my probes Q1, Q2 + `billing.tests.test_overage_requires_payment_intent` + `billing.tests.test_overage_never_expires` | **18 tests OK.** Q1: `h66_lines=1 cap_lines=0 transactions=[]`, so it's refused as keyless and nothing is recorded. |
| **Q2 (observation, as the SM ruled):** what a refused replay leaves behind | After one sweep `('SUCCEEDED', 1)` with 1 ERROR line; after a second sweep still `('SUCCEEDED', 1)` with no further line. The refusal is a handled outcome: logged once, not retried, and the ERROR is the only signal for manual reconciliation. That matches the unpaid refusal. |
| **My mutant Y1** (on `test_vf_h66_mut`, dropped; `PYTHONDONTWRITEBYTECODE=1`; the production file's sha matched the commit blob after the restore) | **KILLED by exactly the two Q1 tests**: d5's adopted test and my probe. d5's other 7 tests **pass under Y1**, which confirms the gap was real before `58d5efb3`. This agrees with d5's own Y1 run at 58d5efb3. |
| d5's gates (cited) | reproduce-first at 552a3bf8: 5 failures (new module only); at c23e71f4: 15 OK and K1–K3 3/3; at 58d5efb3: the module 8 OK, K1–K4 4/4, and Y1 killed by exactly the Q1 test; the regression at a1832485: billing + 9 guards, 2036 OK. |
| Hooks | `pre-commit run --from-ref 67a0681 --to-ref cf7df763` passes, and each of the 5 non-merge commits passes. |
| Merges | `git merge-tree --write-tree` against 67a0681 is **clean**. It is also clean against H-65 @ e3d7751, H-76 @ 78215b89 and H-78 @ b9e4ccb. |

**Disclosure: one run was discarded.**
- My first baseline run (`h66_cf7df763_STALE_PYC.log`, kept) failed both Q1 tests. That run had loaded **stale bytecode from my own pre-review dry-run of Y1**.
- The dry-run applied Y1, `py_compile`d it and restored the file within one second. Y1 only moves lines, so the size is unchanged, and the pyc's whole-second mtime matched the restored source.
- I confirmed it from the pyc header and the cached code's constant order ("cap before H-66"). I then cleared every `__pycache__`, re-ran both steps with `PYTHONDONTWRITEBYTECODE=1`, and the results above are from those runs.
- **The fault was in my scratch worktree, not in d5's code.**
- d5's runner is not exposed to this: each mutant's test run takes seconds between the apply and the restore, so the restored file's mtime always differs.

Logs: `runs/h66_cf7df763.log`, `runs/h66_mutant_Y1_cf7df763.log`, `runs/h66_cf7df763_STALE_PYC.log` (discarded, kept for the record). Probe: `h66_probe_test_vf1a_h66_probe.py`. Mutant: `h66_mutant_Y1.py`.
