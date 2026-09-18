# Free-plan discovery and activation — verification evidence

**Change:** close unrestricted discovery and activation of free/internal plans.
**Branch:** `task/free-plan-activation`. **Implementation commit:** `d883187`.
**Gate-passed commit: `a520422`** (tests + evidence: `883e02b`, `941392f`, `a520422`). **Pre-fix baseline:** beta `b744c9f`.
**Session:** grade-automator-plus-7e (role `fix-free-plan`). **Date:** 2026-09-17.

Every figure below is copied from a log committed under
`docs/evidence/free_plan_activation/`.

## 1. The 10 gates

| Gate | Status | Evidence |
|---|---|---|
| 1 Baseline / Regression | **PASS for this branch; full-repo run DEFERRED TO THE INTEGRATION GATE by design** | `billing` + `users` suites on the final commit: see §8 for counts. No new skips. The full-repository strict gate runs once on the integration commit (session 3e), per the Senior Manager's batching rule. |
| 2 Mutation | **PASS** | 26 mutants, one per guard, **26 killed, 0 survivors**, on `d883187`. All 3 worker baselines green; every restore sha256-verified against the commit blob. `mutation/battery_results_d883187.json` |
| 3 Concurrency | **PASS** | 20 simultaneous requests x 10 rounds = exactly 1 grant per round; 20 mixed self-service/admin threads; signup racing an admin assignment; an orchestrated interleaving that defeats check-then-act. Real threads, real PostgreSQL, each closing its own connection, `is_alive()` asserted after join. |
| 4 Adversarial / Attack | **PASS for the core vectors (independently verified); a bypass battery is still running** | Independent replay by session 7a (red-team-billing), which did not write the fix, committed at `dcc031b` on `task/redteam-billing`: `docs/evidence/security_replay/billing/FREE_PLAN_GATE4.md`. HTTP-only exploits with real JWT against two isolated running apps, accounts and plans created through the real production write paths. **7 vectors, each proven to SUCCEED on `b744c9f` first (H5.2), all refused on the fix** — teacher self-grants BETA on both routes; school admin grants a non-BETA free plan; inactive free plan; 12x repeat (pre-fix: 12/12 accepted, 13 subscriptions and 13 buckets; post-fix: 12x403); plan enumeration (pre-fix: every plan visible to a plain teacher; post-fix: PRO/STANDARD/STANDARD_ANNUAL only). It also probed superadmin admin-assign idempotency 5x per route: first 201 then 400x4, one active subscription, no duplicates — recorded as NOT a finding. I verified its four log checksums against the committed files myself, and its verdict on `d883187` carries to `a520422` because no production file differs between them (`git diff --name-only d883187 a520422` is tests and docs only). **Outstanding:** its bypass battery (field aliases `plan_id`/`user_id`, PATCH/PUT, browsable-API form, trailing slash, select-plan with price 0 or no `stripe_price_id`), run pre-fix first, then against `a520422` exactly. |
| 5 Failure / Recovery | **PASS** | DB failure injected at each write step (subscription row, bucket, ledger): full rollback, prior subscription still active, entitlement not consumed, retry grants exactly once. Stripe down: refusals still 400/403 with no Stripe call. Redis down: select-plan **fails closed** (lock cannot be taken, so no plan change and no Stripe call; a forbidden plan is still a clean 400 because validation precedes the lock); a broker outage in the post-activation `queue_sync` is swallowed by `safe_delay` as designed and the grant still commits exactly once; plan listings do not depend on the cache. Checkout webhook redelivered 3x grants once. |
| 6 Stress / Scale | **PASS, and a performance improvement** | The fix also removes an N+1: plan listings: 9 queries at 8 plans and at 808 plans (flat), 2.7 KB, p50 33 ms. Pre-fix `/subscription/plan` at 808 plans: **809 queries, 343 KB, p50 1,212 ms**. 500-signup burst with BETA-on-signup: flat 30 queries/signup, p50 708 ms, p95 963 ms, 500/500 users with exactly one BETA grant. `scale_fixed.log`, `scale_prefix_b744c9f.log` |
| 7 Real Infrastructure | **PASS (LOCAL-REAL)** | Real PostgreSQL + Redis for every suite. Real Stripe **test mode**: a real customer/price/subscription attacked with 19 requests — app rows and Stripe objects unchanged, **0 Stripe write calls**; the allowed catalog plan still opens a real Checkout Session with the right price and metadata; teardown clean. `stripe_refusal_real.log` |
| 8 Live / End-to-End | **LOCAL-REAL now; DEPLOYED-REAL to be completed on QA after landing** | No deployed run by this session. Per the Senior Manager and the fixes coordinator (2026-09-17), QA (the `beta` deployment, pre-production) is itself the deployed verification stage for this batch: a fix lands on `beta` once its local gates pass and is verified DEPLOYED-REAL on QA afterwards, before any promotion to production. This is a sequencing statement, not a downgrade: no deployed evidence exists yet. |
| 9 Security / Isolation | **PASS** | Whole-payload assertions (no forbidden plan id or name anywhere in any listing, including `?search=`, `?ordering=`, `?page=`) for teacher, school admin, student, licensed teacher and license admin; refusal payloads carry no other user's identifiers; same-school and cross-school bystanders unchanged; single-flag superusers rejected at both the permission and serializer layers. |
| 10 Final Production Gate | **DEFERRED TO THE INTEGRATION GATE by design** | Assigned to session 3e: one strict gate on the integration commit, two consecutive clean full runs. The SHA gated there must equal the SHA landed. |

**Landing rule.** Gates 1 and 10 are satisfied at the batched integration gate,
not here. Gate 4's core vectors are independently verified (see the row above);
it closes fully when 7a's bypass battery reports. Gate 8 is completed on QA after landing, per
the ruling recorded above. Landing itself needs the Senior Manager's review
and, in this session, the user's approval.

## 2. The 8 completion answers

1. **What changed.** Free/internal plans can no longer be discovered or
   activated by ordinary users. `POST /subscription` and
   `POST /user-subscriptions` are superadmin-only; all no-payment activation
   goes through one guarded service; plan listings and `select-plan` share one
   explicit allow-list. Details in §4.
2. **Why it was necessary.** On `b744c9f` any teacher could POST the BETA plan
   id repeatedly and receive a full monthly credit bucket each time
   (10 repeats = 100,000,000 raw credits spent in the replay). School admins
   could take TRIAL and the internal benchmark plan. Every plan was listed to
   every non-student. Inactive plans activated. A Stripe-billed subscriber
   could be moved to BETA in the app while Stripe kept billing.
3. **What was tested.** 101 dedicated tests plus the `billing` and `users`
   suites (1,928 tests, exit 0); 26 mutants; 20x10 concurrency; injected DB failures; a 10-scenario
   attack replay on both trees; scale at two sizes and a 500-signup burst;
   real Stripe test mode.
4. **Which gates passed.** 2, 3, 5, 6, 7, 9, and 1 for this branch's scope.
5. **Which gates remain incomplete.** 4 (core vectors independently verified;
   a bypass battery is still running),
   8 (no deployed run anywhere), and the full-repository runs for 1 and 10,
   which are deliberately deferred to the batched integration gate.
6. **What risks remain.** §7.
7. **Which commit contains the verified implementation.** `d883187` for the
   implementation. `883e02b`, `941392f` and `a520422` add gate tests and
   evidence only. **The gate-passed commit is `a520422`.** The mutation
   battery ran on `d883187`; every commit after it changes tests and docs
   only, so no guard has changed since — verify with
   `git diff --stat d883187 a520422 -- billing/plan_policy.py billing/services.py billing/serializers.py billing/views.py users/signals.py`
   (empty). Doctrine H3.4 therefore does not require a rerun.
8. **Is the verified commit the one intended for release?** No. The release
   commit will be the integration commit built by session 3e, which must be
   gated in its own right.

## 3. Entry points found (discovery and activation)

| Entry point | Before | After |
|---|---|---|
| `POST /user-subscriptions` | `IsTeacher`; any free plan, unlimited repeats | superadmin only (both flags) |
| `POST /subscription` | any non-student; same serializer | superadmin only (both flags) |
| `UserSubscriptionSerializer.to_internal_value` | unfiltered `SubscriptionPlan.objects.get(pk=...)` | resolves only INDIVIDUAL admin-assignable plans; anything else is "Invalid plan id" |
| `GET /subscription/plan` | every plan, to any non-student | superadmin: all. Everyone else: the self-service catalog |
| `GET /subscription-plans` (list/retrieve) | every active INDIVIDUAL plan, to any authenticated user | catalog only; retrieve also allows the caller's own active/pending plan |
| `POST /subscription/select-plan` | block-list (TRIAL/BETA/CUSTOM) | the same allow-list, plus `price_cents > 0` |
| `users/signals.py` BETA-on-signup | `activate_subscription` directly | the guarded service; still only on user creation |
| `SubscriptionService.activate_subscription` | 8 call sites | unchanged for Stripe-driven paths (checkout, upgrade, renewal, reconcile sweep). Each is reached only after a Stripe API mutation or a signature-verified webhook whose `plan_id` metadata was written server-side by `select-plan` |
| `activate_automatic_free_trial` | `select_for_update` on a non-existent row (locked nothing) | locks the user row first; still one trial per user ever |
| `grading_benchmark` command, `isolation_harness` | direct ORM writes, operator-run | unchanged, not reachable over HTTP |
| `qa_console`, `qa_time_travel`, `run_stripe_live_qa` | superadmin + `ENABLE_STRIPE_LIVE_QA` / `ENABLE_BILLING_TIME_TRAVEL` | unchanged |

## 4. Policy per role

`billing/plan_policy.py` holds the allow-lists.
Self-service catalog = name in {STANDARD, PRO, POWER, STANDARD_ANNUAL,
PRO_ANNUAL, POWER_ANNUAL} AND category INDIVIDUAL AND active AND
`price_cents > 0` AND `stripe_price_id` set.
Admin-assignable = catalog names + BETA + CUSTOM, INDIVIDUAL, active.

| Role | Plan listing before | Plan listing after | Activation before | Activation after |
|---|---|---|---|---|
| Student | active INDIVIDUAL plans on `/subscription-plans`; `/subscription/plan` 403 | catalog only; `/subscription/plan` still 403 | 403 | 403 |
| Teacher (individual) | every active INDIVIDUAL plan, incl. BETA/TRIAL/benchmark | catalog, plus their own current plan on retrieve | any free plan, unlimited | none directly; `select-plan` (Stripe) for catalog plans; BETA/TRIAL only automatically at signup |
| Teacher (school licence) | same | same | same | same; the licence-track guard also refuses admin assignment |
| School admin | same | same | free plans via `POST /subscription` | none |
| Superadmin (both flags) | all plans | all plans | any plan | admin-assignable, active plans only; BETA teacher-only and once ever; refused over a live Stripe subscription; refused on the licence track |
| Single-flag superuser | as their `user_type` | as their `user_type` | as their `user_type` | 403 at the view, and refused by the serializer |

`create_superuser()` leaves `user_type=TEACHER`, so both flags are always required.

## 5. Guard-to-mutant map

All 26 killed. Full detail in `mutation/battery_results_d883187.json`.

| Mutant | Guard | Killed by (example) |
|---|---|---|
| M01 | admin plan allow-list | service-level refusal tests |
| M02 | plan lookup scoping (#27) | `test_non_assignable_plans_do_not_resolve_by_id` |
| M03 | active-plan only | `test_inactive_plans_never_activate_even_by_direct_id` |
| M04 | INDIVIDUAL category only | `test_a_license_category_plan_sharing_a_catalog_name_is_refused` |
| M05 | BETA once per user ever | `test_second_beta_assignment_is_refused_on_both_routes` |
| M06 | live-Stripe refusal | `test_a_stripe_billed_subscriber_cannot_be_moved_to_beta_or_any_plan` |
| M07 | live-Stripe status boundary | `test_every_live_stripe_status_blocks_assignment` (PAST_DUE etc.) |
| M08 | per-user row lock | all 3 concurrency tests |
| M09 | BETA teachers only | `test_beta_is_teacher_only` |
| M10 | licence-track guard | `test_licensed_teachers_and_license_admins_are_refused` |
| M11, M12 | superadmin-only on each POST route | ordinary-user refusal tests (403 vs 400) |
| M13, M14 | both-flags check | `test_the_serializer_refuses_single_flag_superusers_on_its_own` |
| M15, M16 | listing filters | discovery tests |
| M17 | retrieve-only widening | `test_a_user_can_still_retrieve_the_plan_they_are_on` |
| M18-M21 | catalog: active, allow-list, price floor, Stripe price | discovery tests |
| M22, M23 | select-plan allow-list and price floor | `test_self_service_selection_uses_the_same_allow_list` |
| M24 | signup grants only on creation | `test_later_saves_never_grant_beta` |
| M25, M26 | signup and admin create use the guarded service | signal tests; 57 failures |

## 6. Production-data uncertainty and impact SQL

Only the local dev database was inspected (2026-09-17): BETA 10,000,000 raw
credits, TRIAL 5,000,000, "Grading Benchmark Plan" 5,000,000, all $0, all
active, all with `carry_over_percent = 0` and no `max_bank`. **Production
values are unknown to this session.** Carry-over matters: with a non-zero
carry-over and no cap, repeated activation also stacked buckets (measured:
5 activations = 50,000,000 raw credits live).

Run these **read-only** queries in production. Nothing here writes.

```sql
-- 1. Which plans are free, unpriced, or not Stripe-linked, and their rollover.
SELECT name, category, tier, interval, is_active, price_cents, monthly_credits,
       carry_over_percent, carry_over_expiry_months, max_bank,
       (stripe_price_id IS NULL OR stripe_price_id = '') AS no_stripe_price
FROM billing_subscriptionplan
ORDER BY price_cents, name;

-- 2. Users who hold more than one subscription row on the same free plan
--    (the signature of repeated free activation).
SELECT us.user_id, p.name, COUNT(*) AS subscription_rows,
       MIN(us.created_at) AS first_seen, MAX(us.created_at) AS last_seen
FROM billing_usersubscription us
JOIN billing_subscriptionplan p ON p.id = us.plan_id
WHERE p.price_cents = 0
GROUP BY us.user_id, p.name
HAVING COUNT(*) > 1
ORDER BY COUNT(*) DESC;

-- 3. Wallets holding more than one MONTHLY bucket granted at a free plan's
--    credit amount (duplicate grants that may still be spendable).
SELECT w.user_id, b.total_credits, COUNT(*) AS buckets,
       SUM(CASE WHEN b.expires_at > NOW() THEN 1 ELSE 0 END) AS still_live
FROM billing_creditbucket b
JOIN billing_creditwallet w ON w.id = b.wallet_id
WHERE b.bucket_type = 'MONTHLY'
  AND b.total_credits IN (SELECT monthly_credits FROM billing_subscriptionplan
                          WHERE price_cents = 0)
GROUP BY w.user_id, b.total_credits
HAVING COUNT(*) > 1
ORDER BY COUNT(*) DESC;

-- 4. Accounts the app shows on a free plan while a Stripe subscription id is
--    attached to their active row (the app/Stripe divergence).
SELECT us.user_id, p.name, us.stripe_subscription_id, us.stripe_status
FROM billing_usersubscription us
JOIN billing_subscriptionplan p ON p.id = us.plan_id
WHERE us.is_active
  AND p.price_cents = 0
  AND us.stripe_subscription_id IS NOT NULL
  AND us.stripe_subscription_id <> '';

-- 5. Non-teachers holding a BETA subscription, and anyone with a
--    subscription on a plan the fix no longer allows to be self-selected.
SELECT u.user_type, p.name, COUNT(*) AS rows_
FROM billing_usersubscription us
JOIN billing_subscriptionplan p ON p.id = us.plan_id
JOIN users_customuser u ON u.id = us.user_id
WHERE p.name NOT IN ('STANDARD','PRO','POWER','STANDARD_ANNUAL',
                     'PRO_ANNUAL','POWER_ANNUAL')
GROUP BY u.user_type, p.name
ORDER BY rows_ DESC;
```

**After the fix, existing history is binding:** anyone who already holds a BETA
row can never be granted BETA again through any path, including a superadmin
assignment. If query 5 shows legitimate accounts that must be re-granted, the
sanctioned route is `ManualCreditService` (admin credit grants), not a second
subscription.

## 7. Remaining risks and decisions for the user

1. **Gate 8 (deployed E2E) has not run**, and gates 1, 4 and 5 are PARTIAL.
   Doctrine H1.3 requires the user's written sign-off to land in that state.
2. **BETA can no longer be granted twice, by anyone.** A teacher who signed up
   while `USE_BETA_PLAN_ON_SIGNUP` was off cannot be given BETA later if they
   ever held it before. Deliberate; confirm it matches the product intent.
3. **An inactive BETA plan is no longer activated at signup.** Previously it
   would have been.
4. **Superadmins can no longer assign TRIAL or internal plans** through the
   generic endpoints, and no license plan at all. Extra credits go through
   `ManualCreditService`.
5. **Plan listings shrank for every non-superadmin.** If any frontend screen
   reads BETA/TRIAL plan details from `/subscription/plan` rather than from
   `/subscription/me`, it will need the `me` payload. The frontend is not in
   this repo, so this was not verified.
6. **Production plan configuration is unverified** (§6).
7. **Unrelated pre-existing finding, reported to the Senior Manager:**
   `run_stripe_live_qa --workers 2` fails `renewals` and `trial_conversion`
   identically on this branch and on `b744c9f`, while `--workers 1` passes
   both. It is a parallel-runner artifact in `billing/live_qa/runner.py` +
   `events.py`, not renewal logic. The nightly Celery task uses the serial
   `run_suite` (`billing/tasks.py:1111`), so the schedule is unaffected; the
   CLI should be pinned to `--workers 1` until it is fixed.

## 8. Run log

| Run | Result | File |
|---|---|---|
| Attack replay on `b744c9f` | 10/10 EXPLOITED | `free_plan_activation/replay_prefix_b744c9f.log` |
| Attack replay on the fix | 10/10 REFUSED | `free_plan_activation/replay_fixed_d883187.log` |
| Mutation battery `d883187` | 26/26 killed, restores verified | `free_plan_activation/mutation/battery_results_d883187.json` |
| Scale, pre-fix | 809 queries / 343 KB / p50 1,212 ms at 808 plans | `free_plan_activation/scale_prefix_b744c9f.log` |
| Scale, fixed | 9 queries / 2.7 KB / p50 33 ms at 808 plans; 500 signups, 30 queries each | `free_plan_activation/scale_fixed.log` |
| Stripe test mode, refusals | app + Stripe unchanged, 0 writes | `free_plan_activation/stripe_refusal_real.log` |
| Stripe test mode, paid paths | 4/6 PASS; renewals + trial_conversion fail only under `--workers 2` | `free_plan_activation/stripe_live_qa_paid_paths.log` |
| Same two scenarios, `--workers 1` | both PASS | `..._renewals_serial_clean_customer.log`, `..._trial_conversion_serial.log` |
| Same two scenarios on `b744c9f`, `--workers 2` | both FAIL identically | `..._prefix_b744c9f_renewal_trial.log` |
| `billing` + `users` suites on `883e02b` | 1,924 tests, OK, 2 skipped, exit 0, clean teardown | `free_plan_activation/billing_users_883e02b.log.gz` |
| `billing` + `users` suites, final, tree identical to `a520422` | **1,928 tests, OK, 2 skipped, exit 0**, no "other sessions using the database" line | `free_plan_activation/billing_users_a520422.log.gz` |
| (discarded) an earlier attempt at that run | 1 failure + 6 errors, all `DeadlockDetected` in unrelated concurrency suites (overage, webhook idempotency, price reconciliation), caused by my own ad-hoc run sharing the same test database. Rerun cleanly above; not counted as evidence either way. | — |
| `manage.py check`, `makemigrations --check`, migration safety, pre-commit | exit 0 each | §9 |

## 9. Static checks

`manage.py check` (0 issues), `makemigrations --check --dry-run`
("No changes detected"), `scripts/check_migration_safety.py --base beta`
("No new migration files in this diff"), and `pre-commit run --files` over
every changed file: all exit 0. This change adds no migration.
