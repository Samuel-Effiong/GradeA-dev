# Codebase Audit Sections — Grade A+ Backend

This document divides the repository into sections so a full code-review +
test-audit pass (see `docs/CODE_REVIEW_STANDARDS.md` for the checklist, and
`docs/prompts/` for the prompts that drive each pass) can cover every part of
the codebase without skipping anything. Go through sections roughly in the
order listed — later sections depend on the models/services defined in
earlier ones.

Each section lists: what it covers, why it matters, and what "done" looks
like for that section's pass.

---

## 0. Cross-cutting (do first — everything else depends on this)

**Covers:** `AutoGrader/` (settings, urls, middleware, celery, dispatch,
health, pagination, error_messages, uploads, cache_utils, request_context),
`manage.py`, `Dockerfile`, `requirements.txt`, `pyproject.toml`,
`.pre-commit-config.yaml`, `.coveragerc`, `.env` / `.example.env`.

**Why first:** this is the shared foundation — settings, middleware, error
handling, and Celery wiring that every app builds on. Getting this wrong
invalidates conclusions drawn about individual apps.

**Done when:** pre-commit passes clean repo-wide; settings are fully
environment-driven; every app's `AutoGrader/tests_*.py` pass; the
`.coveragerc` source list gap (missing `grading`, `ocr_processor`) is
resolved or explicitly deferred with a reason.

## 1. `users` — identity, auth, permissions

**Covers:** `users/models.py`, `services.py`, `views.py`, `permissions.py`,
`serializers.py`, `throttling.py`, `middleware.py`, `renderers.py`,
`mailerlite_service.py`, migrations.

**Why:** every other app's tenancy/authorization model depends on how users,
roles (student/teacher/school admin/super admin), and email-domain rules are
enforced here. Per project memory, personal-email and business-email tracks
are a deliberate, unmerged separation — the review should confirm nothing
introduces a merge path.

**Done when:** authz checklist (§3 of standards doc) passes for every
view/viewset in `users/views.py`; throttling and activation-email domain
rules are covered by their existing tests
(`tests_activation_email_domain.py`, `tests_email_domain_rules.py`,
`tests_open_signup.py`, `tests_superadmin_tenancy.py`).

## 2. `billing` — credits, subscriptions, Stripe, refunds

**Covers:** `billing/models.py`, `services.py`, `stripe_service.py`,
`webhooks.py`, `license_service.py`, `subscription_resolver.py`,
`refunds.py`, `immutable.py`, `access_control.py`, `checks.py`,
`middleware.py`, `qa_console.py`, `qa_time_travel.py`, `live_qa/`,
`billing/tests/`.

**Why:** this is the highest-blast-radius app — real money, Stripe webhooks,
and the append-only ledger just hardened in `e2dc83b`. Errors here are
financial, not cosmetic.

**Done when:** every write path to `CreditLedger` / `CreditUsageLog` is
confirmed append-only; webhook signature verification and timeout sync
(`scripts/check_gunicorn_timeout_sync.py`) are re-verified, not assumed;
`billing/tests/` and `BILLING_SERVICE_REVIEW.md` findings are cross-checked
against current code (that doc may be stale — verify, don't just cite it).

## 3. `classrooms` — enrollment, roster management

**Covers:** `classrooms/models.py`, `services` (via `signals.py`,
`views.py`), `permissions.py`, `serializers.py`, `urls.py`.

**Why:** the tenancy boundary between schools/teachers/students is enforced
here; a leak lets one classroom see another's data.

**Done when:** every queryset in `classrooms/views.py` is scoped by
tenant; `test_bulk_enrollment.py` and `test_views.py` cover the bulk paths,
not just single-record CRUD.

## 4. `assignments` — assignment authoring, PDF pipeline

**Covers:** `assignments/models.py`, `services.py`, `pdf_cache.py`,
`pdf_document.py`, `pdf_renderer.py`, `prosemirror_converter.py`,
`rigor.py`, `schema.py`, `vendor/`.

**Why:** PDF rendering was just changed to load-shed and pre-render on
publish (`ee0a69f`) — confirm the cache/pre-render path is correct under
concurrent load, not just single-request tests. Per project memory, the
Tiptap frontend editor forces full re-extraction on any edit — confirm no
new code path assumes partial/incremental extraction is possible.

**Done when:** `tests_pdf_cache.py`, `tests_pdf_renderer.py`,
`tests_prerender.py` all pass and actually exercise the load-shedding
behavior described in the recent commit, not just the golden path.

## 5. `ai_processor` — extraction, grading, benchmarking

**Covers:** `ai_processor/services.py`, `objective_grading.py`,
`answer_completeness.py`, `evidence.py`, `second_opinion.py`,
`grading_cache.py`, `extraction_schemas.py`, `grading_schemas.py`,
`tools.py`, `benchmark/`, all `*_PROMPT*.txt` files.

**Why:** this is the core product logic and the largest, most test-heavy app
in the repo (30+ `tests_*.py` files). `ai_processor/services.py` has
uncommitted changes as of this audit's start — that file should be the
first thing reviewed in this section, not the last.

**Done when:** every prompt file referenced by `services.py` is confirmed
current (no dangling reference to a superseded `_PROMPT_2` when `_PROMPT_3`
exists); reproducibility/benchmark suites
(`tests_reproducibility_scoring.py`, `tests_grading_benchmark.py`,
`tests_extraction_benchmark*.py`) pass; per standing project rule, any
change to AI-facing code is verified with one real (non-mocked) API call,
not mocks alone.

## 6. `grading` — grading records

**Covers:** `grading/models.py`, `views.py`, `admin.py`.

**Why:** small app, easy to skip — don't. It's also missing from
`.coveragerc`'s `source` list, so coverage numbers for it may be misleading
until that's fixed (see §0).

**Done when:** `grading/tests.py` is confirmed non-trivial (not a stub);
coverage gap from §0 is resolved for this app specifically.

## 7. `students` — submissions, grading dispatch, task tracking

**Covers:** `students/models.py`, `services.py`, `tasks.py`,
`task_context.py`, `task_tracking.py`, `signals.py`,
`second_opinion_serializers.py`, `exceptions.py`.

**Why:** this app owns the Celery task orchestration for the grading
pipeline, including broker-outage recovery and idempotency — the highest
concurrency-risk logic outside billing.

**Done when:** `tests_broker_outage.py`, `tests_grading_idempotency.py`,
`tests_task_tracking.py`, `tests_second_opinion_queue.py` pass and are
confirmed to actually simulate failure/retry, not just the success path.

## 8. `dashboard` — reporting, risk, rigor scoring

**Covers:** `dashboard/models.py`, `services.py`, `risk.py`, `rigor.py`,
`throttling.py`, `at_risk_improvements.py`,
`AT_RISK_IMPLEMENTATION_GUIDE.py`.

**Why:** aggregation queries here are the most likely place for N+1s and
missing pagination (§7 of standards doc) since dashboards summarize across
many rows.

**Done when:** every list/aggregate endpoint in `dashboard/views.py` is
checked for query efficiency; `AT_RISK_IMPLEMENTATION_GUIDE.py` is confirmed
to be documentation-as-code that's still accurate, or flagged if stale.

## 9. `ocr_processor`

**Covers:** `ocr_processor/models.py`, `views.py`.

**Why:** small and thin today, but handles untrusted file input — security
checklist (§3, especially upload validation) applies in full even though
the app is small. Also missing from `.coveragerc` (see §0).

**Done when:** upload validation is confirmed explicit; coverage gap
resolved.

## 10. Templates, static assets, media

**Covers:** `templates/` (including `assignment_to_prosemirror.py`,
`json_converter.py` — code files living in a templates directory is itself
worth a boundary check against §1 of the standards doc), `static/`,
`media/`.

**Why:** `templates/` contains Python modules alongside HTML — confirm this
is intentional packaging, not misplaced code that belongs in an app.

**Done when:** boundary question above is resolved one way or the other and
recorded; no secrets or PII committed under `media/`.

## 11. Root-level repository hygiene

**Covers:** everything sitting loose at the repo root that isn't a
standard project file — duplicate/misnamed HTML test pages
(`google auth test.html`, `google-auth-test.html`, `google_auth_test.html`),
`requieremnt update`, `elf):`, loose PDFs and planning docs
(`Grade A+ Subscription model.pdf`, `Stripe Implementation.pdf`,
`URL Structure.pdf`, `SUBSCRIPTION_FLOW_DIAGRAMS.md`,
`SUBSCRIPTION_FLOW_PLAIN_LANGUAGE.md`, `IMPLEMENTATION_SUMMARY.md`,
`FINAL_VERIFICATION_REPORT.md`, `FUTURE_ROADMAP.md`, `GRADING_FLOW.md` /
`.pdf`, `GRADING_HANDBOOK.md`, `SPECIFICATION_V2.md`,
`API_DOCUMENTATION_LICENSE.md`, `API_LAYER_SUMMARY.md`, `assignent.html`,
`branching-system.png`, `AutoGrader flow.svg`, `stripe commands.txt`,
`QA_SERVER_SETUP.md`), sample data at root (`sample_student.pdf`,
`sample_teacher.pdf`, `Files/`), local/generated artifacts
(`celerybeat-schedule.*`, `.coverage`, `.mypy_cache/`, `.DS_Store`),
environment files (`.env`, `.example.env`, `live.env`, `QA.env`).

**Why:** a cluttered root makes it hard to tell what's load-bearing vs.
leftover, and increases the chance of accidentally shipping something like
`live.env`. This is the literal "removing unnecessary files and documents"
part of the task.

**Done when:** every file above has a documented disposition — keep in
place, move under `docs/`, or delete — proposed to the user for
confirmation (never deleted or moved unilaterally); `.gitignore` is updated
so generated artifacts stop reappearing; confirm none of the `.env`-pattern
files are tracked in git when they shouldn't be.

## 12. `docs/` itself

**Covers:** the existing `docs/` tree, including the large
`docs/backend/` reference set.

**Why:** documentation drifts from code silently. `docs/backend/*.md` files
were generated Aug 26 — confirm they still match the apps they describe
after this audit's findings, especially anywhere this audit found a gap.

**Done when:** any doc found to describe behavior that no longer matches
code is flagged (not silently rewritten — confirm the correct behavior
first).

---

## Task list (tracking template)

Use this table to track progress. Status values: `not started`,
`in progress`, `gaps found`, `clean`.

| # | Section | Status | Notes |
|---|---------|--------|-------|
| 0 | Cross-cutting (AutoGrader core, config, tooling) | gaps found | Review: blocker fixed (`handlers.py` hardcoded `"message": "Not Found"` on every 400/403/404/500 response), plus `.coveragerc`/`.example.env`/Dockerfile gaps — see prior note. Test audit (this pass): read all 12 existing `AutoGrader/tests*.py` files in full and confirmed they assert real values, not just "didn't crash." Added `AutoGrader/tests_send_email_impl.py` (11 tests pinning `_send_email_impl`'s previously-untested branches: successful send, per-recipient vs. flat `merge_data`, the "no fallback possible" raise, and a failure building the message itself), a cache-round-trip-failure test in `test_health.py`, and 3 tests in `tests_celery_signals.py` (missing `sentry_sdk`, falsy `task_id`, cross-context token). **Result: 137/137 `AutoGrader` tests pass, 100.0% coverage (stmt+branch) on every file in `.coveragerc`'s AutoGrader scope** (was 123 tests / 95.7%, with `tasks.py` at only 72.3% before this pass). Cross-checked against sibling apps `billing`, `students`, `users` (imported by `error_messages.py`/`urls.py`) — saw 2 errors in `billing/tests/test_audit_identity_migration.py` which were later traced to a **stale `--keepdb` test database** predating users migration 0036 (`failed_login_attempts`), not to any code defect — once the test DB was rebuilt with current migrations both pass. No action needed in billing. No AI/LLM or billed-API code lives in this section, so the "verify with a real call" requirement doesn't apply here. Open, not fixed (need sign-off): pre-commit hook pins for flake8-bugbear/flake8-comprehensions/flake8-import-order/isort drift from requirements.txt versions; `AutoGrader/tasks.py::send_email_task` retries are not idempotent (a retried send can double-send, now pinned by tests but not fixed). |
| 1 | users | clean | **435 tests pass / 0 fail. Coverage 100.0% — every file, statements AND branches, with no `# pragma: no cover` anywhere** (started the audit at 142 tests / 69.3%). Review-pass fixes: dead `BaseUserViewSet` + `sample_periodic_task`; `verify` 500 on a missing field; `register_student` crash on a null `activation_expires`; dead commented OTP block; `UserActivityMiddleware` throttled to one write per 5-min window with its bare `except: pass` now logging; unreachable `SettingsViewSet.create/destroy` overrides removed (`http_method_names` already refuses POST/DELETE); dead crypto helpers removed from `utils.py`. `change_password` takes an **optional** `otp` (omitted = prior behaviour; supplied = verified, unexpired, constant-time, single-use). **Four real bugs found by the new tests and fixed:** (1) the activity heartbeat used a non-atomic get-then-set, so a 40-request burst wrote 4 rows instead of 1 — now `cache.add` (SET NX), proven by a real-Redis + real-HTTP load suite; (2) every Google sign-up was stored as `registration_method=EMAIL` with a null `email_verified_at` **and was mailed a spurious "verify your email" link**, because the view passed fields the serializer's `Meta.fields` omits and DRF dropped them silently — now injected via `serializer.save()`, deliberately NOT added to the serializer (a writable `email_verified_at` would let any `/auth/register` caller self-verify); (3) an email-registered but unverified account signing in with Google got a 200 plus tokens that SimpleJWT then rejected as "User is inactive" — a dead-end loop, now completed since Google proves mailbox ownership; (4) that fix carries a **carve-out**: an account that was verified and is now inactive was deactivated deliberately, so it is refused with 401 rather than silently re-activated — auto-activating there would have made Google sign-in a ban bypass. Also fixed a flaky test of my own: `tests_activity_middleware` shared the real Redis, where `signals.clear_user_cache`'s `delete_pattern("*user*")` matches the `active_user:` heartbeat key and could wipe it mid-test; pinned to LocMem like every sibling suite. Every fix is mutation-tested (reverting it fails the suite): racy throttle → 3 failures, Google field-drop → 3, ban-bypass → 5, dead-end refusal → 3, OTP check → 5, tenant scoping → 2. Google OAuth's **failure** contract verified against the live provider (token endpoint → HTTP 401 `invalid_client`; forged JWT → `ValueError`); the **success** path needs a human browser consent for a one-time code and remains mock-only — the one stated open risk. Still needs sign-off: `TaskViewSet.task_status`'s unowned `AsyncResult` fallback — only `classrooms.student_summary` depends on it, so convert that endpoint to the tracked pattern first. |
| 2 | billing | gaps found | **915 billing tests pass.** Append-only verified EMPIRICALLY, not by reading: probed every mutation route and found `bulk_create(update_conflicts=True)` silently rewrote a settled `CreditLedger` row (amount 1000 -> 5555). It evades both guards - not `QuerySet.update()`, and `bulk_create` never emits `pre_save`. Closed in `immutable.py` + 6 regression tests (user signed off 2026-09-03). `bulk_update`, related-manager update/delete, cascade-from-user and cascade-from-wallet all confirmed blocked. Both production write paths (`CreditWallet.consume_credits`, `SubscriptionService.refund_credits`) are insert-only plus an `is_refunded` flip, which is the one declared mutable field. **§7 N+1 fixed:** credit-usage-log list issued 24 queries for a 20-row page (`bucket_type` walks `bucket` per row); added `select_related` + a query-budget regression test. Webhook signature verification precedes all state change on both endpoints; `check_gunicorn_timeout_sync.py` passes (100s == 100s); `manage.py check` clean, so billing.E001 (ATOMIC_REQUESTS off — load-bearing for the webhook claim) and W001 both hold. bandit: 0 issues. No `fields='__all__'`, no raw SQL outside migration 0060, every view module declares permission_classes, pagination inherited globally (nothing sets `pagination_class=None`), qa_console double-gated (feature flag + sk_test_ key + superadmin). **BILLING_SERVICE_REVIEW.md is STALE** (dated 2026-06-09): its `validate_admin_user` note says the check merely 'could be stricter' — it has since been hardened to reject SUPER_ADMIN and require school membership, closing a real privilege-escalation path; its Recommendation #4 (scheduled renewal task) is now done and Beat-monitored. Empty `billing/middleware.py` deleted after an exhaustive reference check (no import in any form, absent from MIDDLEWARE, no textual mention anywhere). `BILLING_SERVICE_REVIEW.md` updated to match the implementation. **The `user_id` scoping change was investigated and DECLINED:** the only path that orphans a usage log is deleting the user (`CreditWallet.user` is CASCADE), and a deleted user cannot authenticate — so no live owner is ever denied their history, and superadmins already read unfiltered. Measured both filters directly; the originally-reported failure scenario cannot occur. **Line-by-line pass over `services.py` / `license_service.py` / `stripe_service.py` (F1–F9), all fixes mutation-verified:** F2 partial-renewal rollback (`try` was INSIDE `atomic()`, so a failed teacher's savepoint COMMITTED — they lost a whole cycle's credits while the licence advanced past them); F1 rollover `is_processed` (retired MONTHLY bucket was re-swept by `cleanup_expired_credit_buckets`, double-counting the credits as EXPIRE after they had already been re-granted as CARRY_OVER); F3 overage idempotency scoped to the wallet — **the indexed `BillingTransaction.stripe_payment_intent_id` was REJECTED as the key** because `handle_payment_intent_succeeded` never writes one, so it would have reported "not granted" on every Stripe redelivery and re-granted the credits (pinned by `test_billing_transaction_is_not_a_usable_key`); F5 two division-by-zero paths; F6 audit log discarded by an arg/placeholder mismatch; F7 stale docstring; F8 `idempotency_key` on both Stripe customer creates (`select_for_update()` rejected — callers run outside a transaction, so it would hold a row lock across network I/O). **F4 RESOLVED (was REQUIRES OWNER DECISION):** `calculate_conversion_probability` carried the docstring "Called by midnight" but nothing called it, so the sales-lead endpoints ranked and displayed every teacher at a permanent 0.0. Wired to Beat as `recalculate_conversion_probabilities` at 00:30 (clear of `process-license-renewals` at 00:00); deleting instead would have meant dropping a model field with two indexes, its `Meta.ordering`, two serializer fields and two endpoints — a far bigger diff. Found and fixed a fourth unguarded `initial_beta_credits` division at `views.py:2202` (its three siblings all guard it) that 500'd the WHOLE leads page, not just the offending row. **F9 PARTIALLY RESOLVED (detection added; restructuring still FOLLOW-UP ARCHITECTURAL WORK):** re-audited call by call — receipt fetches never raise and mostly reuse an already-fetched object (latency only); `handle_setup_intent_succeeded`'s `Customer.modify` is idempotent with no DB write after it (nothing to roll back); `sync_price` is set-to-target and the next invoice webhook re-syncs. The one real exposure is `_handle_individual_upgrade_checkout_completed`, which runs `Subscription.modify` and THEN substantial DB work in the same atomic block; its double-refund risk is already covered by an existing `idempotency_key`, leaving Stripe-on-new-price / DB-on-old-plan divergence that **nothing detected** — `reconcile_subscription_renewals` filters `billing_cycle_end__lte=now` (overdue only) and never compares price, while drift sits on CURRENT subscriptions. Added `reconcile_subscription_prices` (Beat 04:30): one `Subscription.list` page per 100 subs rather than a retrieve per row, ERROR per drifted subscription, WARNING for the cycle-boundary race when a change is pending, raises rather than reporting a false all-clear if the listing fails, and makes **no corrective write** — charging the customer what we recorded vs. recording what they were charged is a human money decision. **974 billing tests pass** (was 915; +59 across 7 new files). No migration required (`makemigrations --check`: no changes detected). **TEST AUDIT (this pass): 1026/1026 billing tests pass, 0 failures; coverage 70.1% overall** (was 974 tests / 69.7%), measured with `coverage run --source=billing` against `.coveragerc` scope, not estimated. Scanned all 59 test files programmatically for assertion strength rather than trusting names: found 18 tests with no assertion, 124 asserting only booleans, 37 asserting only a status code — triaged each, and most are legitimate (a `validate_*` helper's contract IS raise-or-not; `access_control` returns booleans; permission tests are *about* the status code). **Three real gaps found and closed, each mutation-proved:** (1) **cross-tenant READ isolation on all four credit endpoints was completely untested** — replacing the `get_queryset` scoping in CreditWallet/Bucket/Ledger/UsageLog viewsets with `pass` and running the whole 974-test suite produced exactly ONE failure, and it belonged to `UserSubscriptionViewSet`; every credit endpoint stayed green while serving every user's rows to every caller, because the existing `test_endpoint_permissions.py` read tests assert only `status_code == 200` (which is what a leaking endpoint returns). New `test_credit_endpoint_tenant_isolation.py` (17 tests) asserts row IDENTITY not counts, covers list + detail-404 + the `?wallet=`/`?bucket=` filter-bypass ordering, and pins that BOTH `is_superuser` AND `user_type == SUPER_ADMIN` are required to unscope — 13 of 17 fail under the mutation. (2) **Webhook signature verification was only ever tested with `stripe.Webhook.construct_event` MOCKED**, so a mis-wired `STRIPE_WEBHOOK_SECRET`, a wrong META key, or removing verification entirely would leave the suite green while letting anyone forge a credit-granting event. New `test_webhook_signature_verification.py` (17 tests) signs payloads with real HMAC-SHA256 and never mocks the verifier: wrong secret, signature-over-different-bytes, missing/empty/garbage header, stale-timestamp replay, single-byte tamper, both fat and thin endpoints, plus proof the thin endpoint never reaches `Event.retrieve` on a bad signature. Mis-wiring the secret → 2 failures; removing verification → 15. (3) **`tests.py::test_create_plan_super_admin_success` asserts nothing** — it builds a payload and `return data` without ever calling the API, so the plan-creation ALLOW path (the row defining every subscriber's credits) had zero coverage while its DENY sibling was real. New `test_plan_admin_api_and_input_validation.py` (12 tests) covers it asserting persisted values, plus adversarial input at the boundary: wrong content-type, malformed JSON, oversized field, negative/non-numeric credits, unknown choice, empty body — each asserting no partial row was written. Also **`test_broker_outage_resilience.py`** (6 tests) for the previously-untested Celery broker-outage path: five of billing's six `.delay()` sites are guarded by `transaction.on_commit(_dispatch)` + try/except, but `tasks.py:442`'s `sync_user_to_mailerlite.delay()` is bare, so a Redis outage is misreported as a *reconciliation* failure — pinned as current behaviour with the rough edge stated, so hardening it forces the summary/logging to be corrected in the same change. **Done-when criteria re-verified independently, not cited:** `scripts/check_gunicorn_timeout_sync.py` RUN → `OK: gunicorn --timeout (100s) matches WEBHOOK_REQUEST_HARD_TIMEOUT_SECONDS (100s)`; append-only re-probed empirically including two routes absent from the existing 30 tests — `update_or_create` on an existing row and async `aupdate()` — both **BLOCKED (ImmutableRecordError)**, with raw SQL confirmed to bypass ORM guards by design; `BILLING_SERVICE_REVIEW.md` spot-checked against current code (its `validate_admin_user` hardening and Beat-schedule claims both hold). **REAL STRIPE CALLS MADE** (sk_test_ key, test mode, read-only): `Account.retrieve()` → `acct_1T8PfJAq8voEV6Ea`; `Price.list()` → 3 prices; and the price-drift reconciler's own extraction path run against live data — `Subscription.list(status="active")` returned 38 real subscriptions and `items.data[0].price.id` resolved on 38/38 under pinned API version `2026-02-25.clover`. **Coverage below the section average, with reasons (NOT test-complete on these):** `stripe_service.py` 62.9%, `views.py` 64.8%, `qa_time_travel.py` 63.4%, `license_service.py` 71.4%, `subscription_resolver.py` 72.7%, `access_control.py` 75.1%, `billing_transaction_service.py` 49.3%, `views_admin_credits.py` 26.9%, `live_qa/scenarios_*` 40-57%, and four management commands at 0.0%. **DEEP-VERIFICATION PASS (2026-09-05): 1098/1098 billing tests pass, coverage 71.2%.** *Stripe, against official docs + the real test account:* webhook signature verification checked line-by-line against docs.stripe.com/webhooks (manual-verification section) — raw `request.body` (Django never mutates it), `HTTP_STRIPE_SIGNATURE`, HMAC-SHA256 over `<t>.<raw body>`, 300s default tolerance, `@csrf_exempt` as the docs require. Added 5 spec-derived tests: **v0-only signature rejected** (Stripe: "to prevent downgrade attacks, ignore all schemes that aren't v1"), **multi-v1 rolled-secret header accepted** (without this, rolling the webhook secret is a 3-day outage), tolerance pinned from BOTH sides, missing-timestamp rejected. 27 signature tests total, none mocking the verifier. *Verified against REAL Stripe test mode (read-only):* API version `2026-02-25.clover` confirmed on both the library and the registered endpoint; top-level `current_period_*` is **ABSENT** on live Subscriptions and only `items.data[].current_period_*` exists — our extraction reads items first, correct; `invoice.subscription` is **ABSENT** and the value lives at `parent.subscription_details.subscription`, which `_resolve_invoice_subscription_id` already handles; price-drift extraction resolved on **38/38** live active subscriptions; **all 11 plan price ids and 9 overage price ids resolve and are active in Stripe**. *Tenant isolation:* swept EVERY billing viewset. Found `BillingTransactionViewSet` — the money ledger at `/invoices/`, with a three-way visibility rule — had **zero tests of any kind**; added 17 (list, detail-404, filter/ordering bypass, school-admin widening, two-signal superadmin rule). Mutations: no scoping -> 12 failures, school branch widened to all schools -> 2, LICENSE granted to everyone -> 2. `PaymentMethodViewSet` re-checked and is sound (customer id always derived from request.user; `_get_owned_payment_method` verifies ownership, already tested). Honest correction: the `.distinct()` on that queryset is **unreachable** — `CustomUser.school` is a FK consumed via `school__in=`, so the join cannot duplicate; removing it fails nothing. Flagged, NOT removed (billing code, own commit). *Broker outage — FIXED, not just pinned:* all 6 billing `.delay()` sites reviewed; 5 already use `transaction.on_commit(_dispatch)` + try/except. The 6th (`sync_user_to_mailerlite.delay` in `reconcile_subscription_renewals`) was bare, so a Redis blip was counted and logged as a RECONCILIATION failure for a subscription that had reconciled correctly. Now wrapped, with a log line stating the billing side succeeded. Reverting the fix fails 2 tests. *Low-coverage review (behaviour, not percentage):* `views_admin_credits.py` 26.9% -> the superadmin **credit-minting API had no tests at all**; added 19 (who may mint, amount priced by the target's own block size, ledger records WHO authorised it, MANUAL_GRANT vs OVERAGE, negative/zero/oversized/unpriceable rejected with no partial write). `billing_transaction_service.py` 49.3% -> `handle_refund` untested; added 16 covering full-vs-partial boundary, invoice-then-PI match order, unmatched refunds creating a flagged reconciliation row, and **REFUNDED status never downgraded by a late PAID event** (Stripe does not guarantee ordering). Mutations: status-downgrade allowed -> 2 failures, `>=` flipped to `>` -> 4, unmatched refund dropped -> 4. `subscription_resolver.py` 72.7% -> `get_monthly_credit_ceiling_for_user` untested; added 9, including that a capped licence teacher is measured against their REAL allocation not the plan default (the plan default shows a progress bar that can never reach 100%). *Append-only re-probed:* 9/9 ORM mutation routes BLOCKED (save, update, delete, instance delete, update_or_create, async aupdate, bulk_update, bulk_create upsert, related-manager update); supported operations still work (insert, the declared-mutable `is_refunded` flip, `allow_unsafe_mutation()` escape hatch **and its restore**); bucket/wallet deletion correctly allowed with audit rows preserved. `billing/immutable.py` NOT modified. Coverage moved where it mattered: billing_transaction_views 61.8->**100.0%**, subscription_resolver 72.7->**92.7%**, billing_transaction_service 49.3->**79.9%**, views_admin_credits 26.9->38.7%, webhooks **98.3%**. **OPEN RISK (config, not code):** the registered Stripe endpoint subscribes to **236 event types while the dispatcher handles 9**, and `_claim_stripe_event` writes a StripeEvent row BEFORE the handler lookup — so 227 unneeded event types each cost 2 DB writes per delivery. Stripe's own docs advise against this. Dashboard change, recommended not applied. |
| 3 | classrooms | not started | |
| 4 | assignments | not started | |
| 5 | ai_processor | not started | |
| 6 | grading | not started | |
| 7 | students | not started | |
| 8 | dashboard | not started | |
| 9 | ocr_processor | not started | |
| 10 | templates / static / media | not started | |
| 11 | root-level repo hygiene | not started | |
| 12 | docs/ | not started | |

This table is the thing to update as each pass of
`docs/prompts/CODE_REVIEW_PROMPT.md` and
`docs/prompts/TEST_AUDIT_PROMPT.md` completes for a section.
