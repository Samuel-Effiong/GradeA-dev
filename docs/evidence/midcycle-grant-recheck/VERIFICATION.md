# Verification: overlapping-run re-checks (mid-cycle grant, trial expiry) @ fc6d0b6

**Verifier:** 1a. **Author:** d5. **Date:** 2026-09-30.
**Branch:** `task/midcycle-grant-recheck` @ **fc6d0b6**, off beta `abeda10`. The code is `ae944e9` (`billing/services.py`, `billing/tasks.py`), with tests in `billing/tests/test_overlapping_run_rechecks.py`. The evidence and the three F6 queries are in `docs/evidence/midcycle-grant-recheck/`.

The run was wrapped (6G, `MemorySwapMax=0`, `nice -n 10`, `timeout -k 60 1800`) in 0b's slot, from a detached scratch checkout at fc6d0b6 with its own test DB (`test_vf_mcg`). Under rule 15, d5's billing regression (1,725 OK) is cited, not repeated.

**Verdict: VERIFIED-WITH-NOTES.** The re-checks are correct and complete for what each task selects on. They cover the **production** conversion path (checkout during a trial), not only the Stripe trial-end path d5 tested. The threaded tests are valid. The switched-off query catches the real production-path harm. The notes are small; none blocks.

## The SM's three questions
**1. Is the threaded race test valid? Yes.**
- **The grant test:** run A holds its transaction open until a lock waiter is seen, and run B is the **real task**. Pre-fix, process_mid_cycle_credit_grant already locked the row; the missing piece was the re-check, which is what fails there.
- **The trial test:** it goes through the **real** `invoice.payment_succeeded` handler (which locks the row) against a stale copy read before.
- **The waiter check:** `pg_stat_activity` is re-snapshotted on each look (`pg_stat_clear_snapshot`), it's scoped to the test's own database, and a timeout **fails** the test instead of passing it silently.
- One looseness, not a defect: it counts any lock waiter in the DB, not specifically the subscription row. Pre-fix, the expiry waits on the trial **bucket** lock (the conversion holds it) instead of the row, which still produces the stale-expiry harm the test asserts against, so the test is valid on both trees.
- d5's 17 mutants include G7/T5 (the row lock removed), and both are killed.

**2. Does the re-check cover the Stripe trial-end path too? Yes. It also covers the production path, in both orders.**
- **The production path is the checkout, not Stripe's trial end.** Trials are local with no card (`activate_free_trial`). A user who pays during a trial goes through `checkout.session.completed → _handle_individual_checkout`. That handler **locks** the trial row filtered on `is_active AND is_trial`, and `finalize_trial_to_paid_conversion` converts **the same row**.
  - **Conversion first:** the expiry now locks and re-reads, sees `is_trial=False`, and returns False. Proven by my **P1**, on both the time path and the credits (`force=True`) path. Pre-fix, both paths **switched the paid subscription off**, and d5's switched-off query reported them.
  - **Expiry first:** the checkout's locked lookup finds no active trial and proceeds as a new signup (pre-existing design; unchanged).
- **Stripe trial-end conversion** (`finalize_trial_conversion_via_stripe` via `invoice.payment_succeeded`, which locks the row):
  - **Conversion first:** covered (d5's threaded test).
  - **Expiry first:** see N1. It isn't reachable in production.
- **Other callers of `expire_trial`:**
  - `invoice.payment_failed` locks the row and only calls `expire_trial` while `is_trial` is set. After an expiry it takes the non-trial branch: **no second EXPIRE row** (my P3). P3's pre-fix failure is only the new `True` return contract.
  - `customer.subscription.deleted` only looks up active rows.

**3. Could the three read-only queries miss real cases, and are they safe?**
- **Safety:** all three are `SELECT`s only (d5's test asserts they are read-only). The outputs are ids, counts and timestamps, with no emails.
- **detect_paid_subscriptions_switched_off:** the **checkout** conversion records the same `INDIVIDUAL_TRIAL_CONVERSION_CHARGE` against the same row, so the query covers the production path.
  - My P1 on abeda10 shows it **reporting the real harm** produced through `_handle_individual_checkout`, on both expiry paths.
  - My P2 shows it reporting the harm as the pre-fix expiry wrote it.
  - It holds up because nothing re-saves the harmed row afterwards: `customer.subscription.updated` and `.deleted` look up only active rows, so `updated_at` and `stripe_status` stay put.
  - The pre-fix race serialises on the trial bucket lock, so `updated_at` (the deactivation) falls after the conversion charge.
  - **One real gap (N1):** the expiry-first order on the Stripe trial-end path records the charge as `INDIVIDUAL_SUBSCRIPTION_CHARGE`, so the query does not see it (P4: `rows=0`). That order isn't reachable in production.
- **detect_double_midcycle_grants:** the next grant is `now + 1 month` (`services.py:771`), not the previous due date plus one month. So a subscription that fell behind can't produce legitimate grants less than 14 days apart, and the threshold gives no false positives from catch-up. An overlap writes both grants seconds apart, so none are missed. The rollover reference string matches the code (`"Mid-cycle rollover within annual plan …"`).
- **detect_duplicate_trial_expiries:** there is one legitimate EXPIRE per trial bucket, from the conversion forfeit, the cleanup or the expiry. So `COUNT > 1` is exact. The conversion forfeit followed by a pre-fix stale expiry shows up here too.

## Evidence
My probes are in `midcycle_probe_test_vf1a_midcycle_probe.py`, and the harness is `midcycle_harness_vf_mcg_run.py`.

| Check | Result |
|---|---|
| **Baseline** @ fc6d0b6: my 5 probes + d5's 21 tests | **26 OK** |
| **Reproduce-first:** abeda10's `billing/services.py` + `billing/tasks.py`, my probes | **P1 time path and P1 credits path FAIL**. The paid subscription is switched off after a **checkout** conversion, and the switched-off query reports it (subscription, user, conversion transaction). P3 fails only on the new return contract. |
| P2: the query on the checkout-path harm as the pre-fix expiry saved it | Reported. *My first attempt used `QuerySet.update` without `updated_at`, which the real save sets; that was my fabrication's bug, not the query's. Fixed and re-run.* |
| P4 (documenting): expiry first, then the Stripe trial-end payment | `is_active=False`, the charge recorded, `switched_off_query_rows=0` (N1) |
| **Mutant (mine), 1/1 KILLED:** X1, where the re-check is skipped on the `force=True` credits path | Killed **only by my P1 credits-path probe**; d5's battery has no stale-copy test on the credits path |
| Hooks | `pre-commit run --from-ref abeda10 --to-ref fc6d0b6` passes, and each of the 2 commits passes |

## Notes
- **N1 (not reachable in production today; record it).** On the Stripe trial-end path, if the expiry commits **before** Stripe's `invoice.payment_succeeded` arrives, the handler finds the row inactive. It records the money as `INDIVIDUAL_SUBSCRIPTION_CHARGE` and skips renewal, so the customer has paid but is switched off, and the switched-off query doesn't see it. Stripe finalises a trial-end invoice about an hour after trial end, and `expire_active_trials` runs every 6 hours. So any trial that carries a Stripe subscription with a Stripe-side trial would hit this about 1 time in 6. Today only live QA creates such trials (`stripe_live_qa_scenarios.py`, `trial_period_days`). **If a card-on-file trial is ever introduced, `expire_active_trials` must leave Stripe-trialing rows to the webhooks.**
- **N2 (tests, low).** Consider adopting P1 (the production checkout path, both expiry paths). It is the only test that kills X1.
- **N3.** The waiter check matches any lock waiter in the test DB, not the specific row (see Q1). It's valid as written; it could filter on `query ILIKE '%billing_usersubscription%'` to be exact.

## Bundle 4 (F6 item 3)
`git merge-tree --write-tree 8de3078 fc6d0b6` (bundle 4's tip) is **clean**. It shares no files with the add_teachers fix (`license_service.py`) or the H-38 tasks fix (`assignments/tasks.py`, `users/views.py`). If the founder picks the fold-in, it needs one strict full re-run and my Gate 1 refresh.

Logs: `runs/midcycle_{baseline_fc6d0b6,prefix_abeda10,mutant_X1}.log`.
