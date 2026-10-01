# Verification: H-28 Change 1 (licence Stripe mutations) @ 53b8378

**Verifier:** Verification Engineer (1a, grade-automator-plus-c2). **Author:** Hardening (d5).
**Branch:** `task/h28-p1b` @ `53b8378`, off H-62 `f3002bc`, with bundle 4 merged (`8e450e6`). Everything after `e70d641` is docs. **Date:** 2026-09-30.

**Verdict: REJECTED at 53b8378, on two test-only items (R1, R2); VERIFIED-WITH-NOTES at 1109ffd after the fix (see the re-check at the end).** The code held up under every attack, including real-HTTP retries. But two tests on the SM's focus points can pass without proving what they claim. Both are small fixes, and a narrow re-check follows (R1/R2's modules plus my two mutants).

## What I checked
My own detached checkout with its own test DB, `EXEMPT_EMAIL_DOMAINS` empty, and every run under `systemd-run` MemoryMax=6G, `nice -n 10`, `timeout`, in 0b's slot beside Gate 10.

### History, hooks, migration
| Check | Result |
|---|---|
| Merges in `f3002bc..53b8378` | All 5 are **clean** (`merge-tree` equals the merge): `8e450e6` (bundle 4) and the four bundle-4 merges under it (`bd29d1f`, `1afebe4`, `98b0ddf`, `ede7101`), all already verified. |
| Whole-range hooks | `pre-commit run --from-ref f3002bc --to-ref 53b8378`, my own run: **rc 0**, nothing rewritten. It agrees with d5's `port_range_hooks.log`. |
| Per commit (each commit's own files at that commit) | **26 of 26 first-parent commits pass.** The known non-bisectable `cb2e657` also passes its hooks: its defect is the migration graph (0072 still depended on 0069, giving three leaves), which no hook checks. `8aada3c` fixes it. No commit in the range used `--no-verify`. |
| Migration 0072 | A single `CreateModel` (`LicenseStripeMutationIntent`, with its indexes and a conditional `UniqueConstraint`, the per-licence guard). It depends on H-62's `0071`, and `billing` has one leaf. It creates a table only, so H-56's rollback guard doesn't apply (the previous release never writes this table). |
| Rule-15 addendum (model changes) | `billing/models.py` only **adds** the new model and its two enums; no existing model changes. Its readers are `billing` (the flows and admin) and the settings beat entry and its health row, whose readers (`AutoGrader.tests_beat_health`, `test_health`) ran in the changed modules. So `billing` is the right owning-app regression. |

### Rule-15 runs
| Run | Result |
|---|---|
| Changed modules at `53b8378`: d5's set (the 9 H-28 modules, `test_mailerlite_sync`, `test_live_qa_scenario_registry`, `AutoGrader.tests_beat_health`, `test_health`, `tests_migration_rollback_defaults`), plus `test_license_cancellation` (retargeted) and my 3 real-HTTP probes | **Ran 208, OK**, 230 MB peak |
| Author regression (rule 15) | Relied on d5's committed logs, not re-run. `app_billing_2e770c2.log`: **1882 ran; the only 5 failures were in `test_license_cancellation`** (3 errors, 2 failures: the stale stubs). `touched_modules_e70d641.log`: that module and the three strengthened ones, **50 OK**. |

### The SM's focus points
| Focus | Finding |
|---|---|
| **Timeout arithmetic and the 1–30 s clamp** | `call_timeout_seconds` = ((time left − 1 s retry sleep) × 0.9) ÷ 2 attempts, clamped to [1, 30]. With the full 75 s budget: 33.3 → **30 s**. The production worst case per call, 2 × 30 + 1 = **61 s < 75 s**. Mutants killed by d5's sizing test: V4 (no division: 35.2 s), V5 (no upper clamp: 33.3 ≠ 30), V6 (no lower clamp: −0.225 ≠ 1), V7 (no sleep reserve: 4.5 ≠ 4.05). V4 and V7 also make the real-socket test's outer wait fire. |
| **contextvars copy (L29)** | `call_stripe` runs its worker via `contextvars.copy_context().run`. My independent re-run, **V8** (the copy removed), is killed by `test_a_hung_stripe_fails_at_the_socket_inside_the_budget`: the outer budget fires, because the worker saw no deadline and used 30 s. |
| **The direct-`stripe.*` scan** | `NoLegacyStripeCallTests` passes, and today the only direct Stripe object in the licence flows is the adapter's own `StripeClient` (my AST sweep of `license_service`, `license_stripe_mutation` and `stripe_service`). But the scan is narrow; see N1. |
| **`max_network_retries=1` vs idempotency** | stripe-python 14.4.1 (`_api_requestor.py:544-550`) sends our `idempotency_key` when given, and adds a uuid key to **every POST** otherwise, reused on its own retry. **DELETE gets no key**. **Probe R1 (real HTTP):** the first POST's connection is dropped after the request arrives, stripe-python retries, and **both attempts carry the intent's own `h28-licence-<id>-apply`**; the cancel completes. **Probe R2:** a DELETE whose first attempt landed is refused on retry as "No such subscription"; `read_back_on` reads it back, finds `canceled`, and the conversion completes. **R3 (control):** the same refusal with the read-back showing `active` leaves the intent FAILED and the licence on Stripe. Mutants: V9 (POST key dropped) is killed by d5's adapter tests and R1; V10 (no network retry) by R1, R2 and the adapter test; V11 (delete not read back) by d5's convert tests and R2. |
| **The 5 retargeted stubs in `test_license_cancellation`** | The four success-path tests now assert `LicenceStripe.modify_subscription` called with `idempotency_key=ANY`, so they cannot pass without the adapter being used (V1 kills them). **The failure-path test still can: R1 below.** |
| **The 4 strengthened tests (e70d641)** | Replayed in my own checkout, each is killed by its new test, for the right reason. **L04**: the intent went FAILED (not PENDING) before the undo. **L07**: an older open invoice was voided ('void' ≠ 'open'). **L15**: an old ESCALATED intent alerted again (1 ≠ 0). **L20**: "a retry reused the connection" (1 ≠ 3 backend pids). |
| **`resolve_licence_stripe_intent`** | By reading: it imports neither `stripe` nor `LicenceStripe`; it is a dry run unless `--apply`; `--apply` needs `--outcome`, a non-blank `--note`, and `--by` naming an active super admin; it writes only the intent row, through a conditional UPDATE fenced on the status it read. Mutants: V12 (applies by default) and V14 (calls `LicenceStripe`) are killed. **V13 (writes the licence) survives: R2 below.** |
| **The named Gate-5 tests** | The kill is `pg_terminate_backend(pg_backend_pid())` on the test's own raw connection. It asserts `current_database()` is its own `test_…` DB and that the retry committed on a different pid. Nothing in `billing` looks up another backend to kill. The budget test makes every call slow. |
| **`change_license_price`** | It now only delegates to `LicenseSubscriptionService.change_license_plan` (None meaning the plan's own price). It has no production callers left, and there's no recursion (the plan change uses `apply_licence_price_at_stripe`). |

## Required before merge (test-only)
**R1. `test_license_cancellation.test_stripe_failure_is_surfaced_and_leaves_local_state_untouched` can still pass vacuously.** It sets `mock_modify.side_effect` but never asserts the mock was called. My **V1** routes cancel back to the legacy `stripe.Subscription.modify`: every other test in the module fails, **this one passes**, because the H-39 network guard's refusal surfaces as the `ValueError` it expects. That is exactly how it passed before `e70d641`. Fix: `mock_modify.assert_called_once()` (or the same `assert_called_once_with(..., idempotency_key=ANY)` as its siblings) after the `assertRaises`.

**R2. "The resolve command never changes the licence" is under-tested.** `test_apply_closes_the_intent_and_frees_the_licence` compares 4 licence fields (`max_seats`, `auto_renew`, `plan_id`, `billing_method`). My **V13** (the command sets `is_active=False` on the licence) **survives**. Fix: compare the whole licence row before and after (for example `model_to_dict`, or every concrete field except `updated_at`), so that a write to any field fails. The command itself is correct today.

## Notes (not blocking)
**N1 (the scan is narrow).** `NoLegacyStripeCallTests` scans only the named flow functions, matches only the literal `stripe.Subscription|Invoice|Price.x`, and doesn't flag a bare `StripeClient` built outside the adapter. **V2** (a direct call in a helper the flow calls) and **V3** (`__import__('stripe').Subscription.modify`) survive the scan. Both are latent: my sweep found no such call today, and the flows' behaviour tests catch a rerouted call (V1). Widening the scan to whole modules, or checking every name bound to the `stripe` module, would close it. The no-wildcard guard has the same limit (bundle 3's L1/L2).

**N2 (outside H-28's four operations).** `StripeCustomerService.get_or_create_license_customer` (`stripe.Customer.create`) and `create_license_setup_intent` (`stripe.SetupIntent.create`) still use the legacy API, with its 80 s × retries timeout. They are licence set-up paths, not mutations of an existing subscription, so they are out of Change 1's scope. They're worth a backlog row if the request budget should cover them.

**N3 (residual, stated by d5).** `requests`' timeout bounds each socket read, not the whole response, so a reply trickling in slower than that could keep an abandoned thread alive past the socket bound. The outer wait still bounds the request, and Stripe is the peer. Accepted as documented.

**N4.** Gate 7 against Stripe test mode, including the §9j question of how Stripe answers a DELETE retry, is out of scope by design. R2 proves the code's handling of a refusal of the shape described there, over real HTTP.

## Probes and mutants
In `GAP-1a-records/`: `h28_test_vf_h28_probe.py` (R1–R3; loopback only, 127.0.0.1:0, a fake `sk_test_` key), `h28_vf_mutants.py` (18), `h28_vf_runs_SUMMARY.txt` and `h28_vf_hooks_percommit.log`. Every mutant restore was sha-checked.

| Mutant | Result |
|---|---|
| V1 cancel via the legacy API | killed (5 cancel tests + the scan), **but not by the failure-path test (R1)** |
| V2 a direct call in a helper / V3 an aliased module | **survived** the scan (N1) |
| V4–V7 the timeout arithmetic and clamps | killed |
| V8 the contextvars copy removed | killed |
| V9 POST key dropped / V10 no network retry / V11 delete not read back | killed (d5's tests and my R1/R2) |
| V12 resolve applies by default / V14 resolve calls Stripe | killed |
| V13 resolve writes the licence | **survived (R2)** |
| L04 / L07 / L15 / L20 replays | killed, each by its strengthened test |

---

## Re-check @ 1109ffd (R1, R2 and N1 fixed), 2026-09-30: **VERIFIED-WITH-NOTES**
The delta from `53b8378` is `f0169ca` (test-only: `test_license_cancellation`, `test_h28_alerting`, `test_h28_stripe_budget`) and `1109ffd` (docs). No production code changed.

| Check | Result |
|---|---|
| Hooks, `pre-commit run --from-ref 53b8378 --to-ref 1109ffd` | rc 0 |
| The 3 touched modules at `1109ffd` | **Ran 43, OK** |
| **R1**: V1 (cancel routed back to the legacy API) | **killed, now including** `test_stripe_failure_is_surfaced_and_leaves_local_state_untouched` (it asserts the adapter was called once with the intent key). **Closed.** |
| **R2**: V13 (the resolve command writes `licence.is_active`) | **killed** by `test_apply_closes_the_intent_and_frees_the_licence`, which now compares every concrete licence column except `updated_at`. **Closed.** |
| N1: V3 (an aliased module, `__import__('stripe')`) | **killed** by the widened scan. It now flags any `stripe.*` use but `stripe.error`, any run-time import, and any `StripeClient` outside the adapter, with a guard-on-guard for each. |
| N1: V2 (a direct call in a helper outside the scanned flows) | still **survives** the scan. It is latent (nothing does it today), a rerouted cancel is caught by the behaviour tests (V1), and d5 states it in `PORT_H62.md`. |

Remaining notes: N1 (V2, stated and latent), N2 (the licence customer and set-up-intent calls outside Change 1), N3 (per-read socket timeout vs a trickling reply), N4 (Gate 7 in Stripe test mode is out of scope). None blocks the merge. The log is `h28_vf_recheck_1109ffd.txt`.
