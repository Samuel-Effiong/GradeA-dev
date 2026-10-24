# H-28 / P1b — irreversible Stripe mutations inside transactions

**STATUS: WIP — PHASE 1 INVESTIGATION, INCOMPLETE. NOT EVIDENCE YET.**
No gate has been run. No design is proposed. No code has been changed.
Nothing in this file may be quoted as a verified result.

| Field | Value |
|---|---|
| H-number | H-28 (P1b) |
| Owner | fix-p1b (grade-automator-plus-72 [9ce623]) |
| Branch | `task/p1b-divergence` |
| Tree audited | `b744c9f706840d27d5ec28b5be178c80253a5066` (beta) |
| Gate-8 class | ENVIRONMENT-SENSITIVE / billing |
| Scope ruling | d4, 2026-09-17: scope is **the whole class across `billing/`**, webhook AND view layer — NOT the board's fixed 6 sites. This audit DEFINES the scope. |

## The bug class

Django rolls back the DB half of a failed operation. **Stripe does not roll
back.** An irreversible Stripe mutation executed inside a `transaction.atomic`
that later aborts leaves **Stripe changed and the DB unchanged** — a permanent
divergence no retry repairs.

Distinct from H-23 (e2's P1, money-loss): there a lock is held across a Stripe
call and a Postgres 60 s `idle_in_transaction_session_timeout` kills the session,
rolling back a PAID grant Stripe never redelivers. **Here the Stripe call
SUCCEEDS and cannot be undone.** e2's Design A does not cover this.

**Two different failure modes, because two different kill points:**
- **Webhook/worker layer** — Postgres `idle_in_transaction_session_timeout`
  (60 s on PROD and BETA, 0 on sandbox), plus webhook redelivery replaying a
  mutation a concurrent run already made.
- **View layer** — gunicorn's request `--timeout` hard kill, a client
  disconnect, or any later exception in the same view. No redelivery at all,
  so divergence here is silent and permanent unless reconciled.

## Method

`docs/evidence/h28_p1b/p1b_audit.py` (committed alongside), an AST pass over
`billing/**/*.py` excluding tests, migrations, live_qa and qa_*:

1. **Lexical** — every Stripe MUTATION call (state-changing only; `retrieve`,
   `list`, `construct_event` excluded), with the enclosing function and whether
   an `atomic` block or `@atomic` decorator lexically encloses it.
2. **Transitive** — a name-based call graph, propagating "reaches a Stripe
   mutation" up through callers, to catch a transaction opened in one file
   wrapping a mutation made in another (the `views.py:726` shape).

Name-based resolution is **deliberately over-inclusive**: it is a funnel, and
every hit must be confirmed by hand against the source before it enters
evidence. Raw output: `p1b_audit_b744c9f.json`
(sha256 `d2a80b6b1613b70e0eac73c0640dc735891a7c1fff927d3b6ec10889c8ffb43c`).

## Raw counts (tool output, tree b744c9f)

| Measure | Count |
|---|---|
| Stripe mutation call sites in `billing/` | 31 |
| ...**lexically inside** an atomic block | **7** |
| `atomic` blocks total | 15 |
| `atomic` blocks reaching a mutation (incl. transitive, over-inclusive) | 11 |

## A. Lexically inside atomic — 7 sites, tool-identified, HAND-CONFIRMATION PENDING

| # | Site | Stripe call | Function | atomic opens |
|---|---|---|---|---|
| A1 | `billing/views.py:766` | `Subscription.modify` | `cancel()` | **:726** |
| A2 | `billing/stripe_service.py:1054` | `Subscription.modify` | `reactivate_if_cancelling()` | :1010 |
| A3 | `billing/stripe_service.py:4691` | `Customer.modify` | `handle_setup_intent_succeeded()` | :4655 |
| A4 | `billing/license_service.py:1969` | `Subscription.modify` | `cancel_license_subscription()` | :1922 |
| A5 | `billing/license_service.py:2224` | `Subscription.modify` | `update_seats()` | :2175 |
| A6 | `billing/license_service.py:2249` | `Subscription.modify` | `update_seats()` | :2175 |
| A7 | `billing/license_service.py:3465` | `Subscription.delete` | `convert_license_to_offline()` | :3450 |

**A1 also holds a `select_for_update()`** (`UserSubscription`, :728-730) across
the network call — so it carries H-23's money-loss shape *as well*, at a view-layer
door e2's webhook-focused audit did not cover. This is the finding that widened
the scope.

## A-CONFIRMED. Hand-confirmation of section A (2026-09-18, read against `b744c9f`)

Each site below was READ, not inferred. Tool output alone is not evidence (H1.4).
Still: **confirmed by reading, NOT yet reproduced by a test, NOT fixed, no gate run.**

| # | Site | Verdict |
|---|---|---|
| A1 | `views.py:754` + `:766` | **CONFIRMED DEFECT** |
| A2 | `stripe_service.py:1054` | **NOT A DEFECT — this is the correct pattern** (reclassified) |
| A3 | `stripe_service.py:4691` | **LOW / likely benign** |
| A4 | `license_service.py:1969` | **CONFIRMED DEFECT** |
| A5/A6 | `license_service.py:2224`, `:2249` | **CONFIRMED DEFECT (money)** |
| A7 | `license_service.py:3465` | **CONFIRMED DEFECT — worst of the set** |

The board's original priority site (`stripe_service.py:3399`) is **not yet hand-confirmed.**

### A7 — `convert_license_to_offline()` — whole-school revenue loss

`@transaction.atomic` (decorator, :3449) + `select_for_update` on `LicenseSubscription`,
then **`stripe.Subscription.delete(...)` at :3465**. A Stripe subscription delete is
**irreversible** — there is no un-delete; recovery means creating a new subscription.
Every DB write follows it inside the same transaction: `billing_method=OFFLINE`,
`stripe_subscription_id=None`, `stripe_status=None`, and a `LicenseBillingRecord` row.

Rollback after the delete (60 s idle-in-transaction kill on a slow Stripe call, or any
later raise in the block) leaves: **Stripe subscription permanently gone; app still
believes the school is STRIPE-billed and still holds the dead `stripe_subscription_id`.**
The school stops being billed and nothing reports it. Admin action at the **view layer,
so never redelivered** — silent and permanent.

### A5/A6 — `update_seats()` — money, and a compensator that can be skipped

`@transaction.atomic` (decorator, :2174) + `select_for_update`. `Subscription.modify`
at :2224 changes seat quantity; under `proration_behavior="always_invoice"` Stripe
**charges the school immediately**. A compensating revert exists at :2249 — but it is
itself a Stripe call **inside the same transaction**, so a mid-call kill skips it. Rollback
leaves the school charged and upgraded at Stripe, with the app on `old_seats`.

### A4 — `cancel_license_subscription()`

`@transaction.atomic` (decorator, :1921) + `select_for_update`. `cancel_at_period_end=True`
set at Stripe (:1969); `auto_renew=False` written locally afterwards. Rollback = Stripe will
not renew, app says it will.

### A1 — `views.py` `cancel()` — the original find, holds up

`with transaction.atomic()` (:726) + `select_for_update` (:728-730) held across **two**
irreversible Stripe calls: `release_schedule` (:754) and
`Subscription.modify(cancel_at_period_end=True)` (:766). Teacher cancels; Stripe cancels;
DB rolls back; app still shows renewing — or the pending plan change is released at Stripe
yet still displayed as pending. **View layer, never redelivered.**

Note the explicit `StripeError` branch (:768-795) writes the schedule-clearing fields and
then `return`s — a `return` inside `atomic` **commits**, so that path is sound. The hazard
is the *kill*, not the handled error.

### A3 — `handle_setup_intent_succeeded()` — LOW, and why

`Customer.modify` (:4691) is inside the `@transaction.atomic` decorator (:4655), but
**nothing follows it except logging** — no DB state is lost on rollback — and redelivery
re-sets the same default payment method idempotently. Different severity from the rest.
**Confirms these 7 are not one defect and must not get one uniform remedy.**

### A2 — NOT A DEFECT — the in-tree reference pattern

`reactivate_if_cancelling()` (:975-1070) already does it right, and my tool mis-flagged it:
- the real `Subscription.modify` is **OUTSIDE** the transaction (~:998);
- the atomic block (:1010) is **short and purely local**;
- the call at :1054 is a deliberate **compensating revert** on local-save failure;
- when the compensation itself fails it logs **"MANUAL RECONCILIATION NEEDED — Stripe and
  local state now disagree about renewal."**

**The house already contains the remedy.** The design proposal will cite :975-1070 as the
in-tree reference — mutate Stripe outside the transaction, keep the transaction short and
local, compensate on failure, log loudly when compensation fails — rather than inventing a
pattern. Cheaper to approve, consistent with house style.

## RECONCILIATION HALF 1 — "has this already happened?" (read-only, 2026-09-18)

Approved by d4 via 95. Script `reconcile_half1_readonly.py`; log
`reconcile_half1_beta_20260918_1008.log`
(sha256 `37ab2161e17bd248cefed2541a338f3ed3f9b0d901c59f2962152f7582deb746`).
Read-only psycopg2 session (`set_session(readonly=True)`), SELECTs only, no writes,
no Stripe calls. (A first attempt failed on a wrong table name — `schools_school`;
the model lives in `classrooms`. Re-run after correction; only the complete run is kept.)

### !! WHAT THIS DOES AND DOES NOT ANSWER — READ BEFORE QUOTING IT !!

**1. This is the QA BETA database, NOT production.** `DATABASE_URI` resolves to
`switchback.proxy.rlwy.net` = `grade-automator-beta-production`, the deployed QA beta.
**There is no production DB credential in `.env` at all.** So this result says nothing
about whether a real *paying* school has been hit. **It cannot answer the meeting
question** ("has a real school already lost its subscription?") — only the beta-stage
version of it.

**2. The query is blind to the actual fingerprint, by construction.** If
`convert_license_to_offline()` dies after the Stripe delete, the local writes never run,
so the row rolls back to **`billing_method=STRIPE` with `stripe_subscription_id` STILL
POPULATED** — indistinguishable in the DB from a perfectly healthy licence. The "STRIPE
but no id" shape this query looks for is therefore *not* what the bug leaves behind.
**An empty or clean half-1 result is NOT an all-clear.** Only half 2 (live
`Subscription.retrieve` per school) can answer it.

### Results (beta)

| billing_method | is_active | count | missing `stripe_subscription_id` |
|---|---|---|---|
| OFFLINE | true | 5 | 5 |
| STRIPE | false | 1 | 0 |
| STRIPE | true | 1 | **1** |

The one "STRIPE but no id" row: licence `9f83df46-6159-420f-96bc-4633ef4616e7`,
school `Unperplexed Consulting`, active, auto_renew, `stripe_status=None`,
**`has_customer_id=False`**, updated 2026-07-18.

**This row is NOT a P1b victim, and I am not reporting it as one.**
`convert_license_to_offline()` clears `billing_method`, `stripe_subscription_id` and
`stripe_status` but **never touches `stripe_customer_id`** (:3471-3481). A school that
had ever been Stripe-billed would therefore still hold a customer id. This row has none,
and no `stripe_status` — it looks like a licence that was never wired to Stripe at all
(incomplete setup), not a conversion that died. **Flagged as a separate data anomaly,
outside H-28.**

### Half-2 sizing (for d4, before any Stripe call)

**1 candidate row** (`billing_method=STRIPE` with a non-blank id), of which **0 active**.
Trivially cheap — one `Subscription.retrieve`, rate limit irrelevant at this size.
**Not started; awaiting d4's go per the stated condition.**

`CONVERTED_TO_OFFLINE` audit records on beta: **0**. Note this row is written *inside*
the same transaction as the delete, so a death mid-flight leaves none — a zero is
consistent with both "never ran" and "ran and died".

## B. Transitive candidates — SUPERSEDED. My v1 figures were WRONG BOTH WAYS.

**Correcting my own earlier numbers before anyone quotes them.** The "15 atomic blocks,
11 risky" figures published in the first commit (`5ba468e`) are withdrawn. The v1 tool was
wrong in **both** directions:

1. **UNDER-reported: it ignored `@transaction.atomic` DECORATORS in the transitive pass.**
   Only `with` blocks were recorded. A decorator is an atomic block over the whole function
   body, so every decorated handler was invisible. Real count: **66 atomic blocks, not 15.**
   **This gap hid the board's own priority site** — `stripe_service.py:3399` sits in a helper
   called by the `@transaction.atomic`-decorated `handle_checkout_completed`.
2. **OVER-reported: the name-based call graph manufactured false paths.** A bare `create()`,
   `record()` or `list()` cannot be told from Django's ORM methods, and `create` *is* also
   the name of a function that calls `stripe.SetupIntent.create` — so "reaches a mutation"
   propagated through half of `billing/`. Unfiltered, the fixed tool reported **52** rows,
   nearly all false.

Fix: names that are ORM/builtin-shaped or defined more than once are **not propagated and
not reported**, and are listed separately in the JSON as `ambiguous_names_not_propagated`.
This trades recall for credibility — a name collision could in principle hide a real path,
so the lexical pass (section A) remains the authority and section B is a supplement.

Current output: **4 transitive paths, all unambiguous** (JSON sha256
`59dc9b12913c359d94b29dd5996c38c78aa839f292da13af8962899a05b358b7`):

| Atomic block | kind | SFU | Reaches |
|---|---|---|---|
| `stripe_service.py:2784` `handle_checkout_completed()` | decorator | no | `_handle_individual_upgrade_checkout_completed()` :3318 |
| `stripe_service.py:3719` `handle_invoice_payment_succeeded()` | decorator | **yes** | `_handle_individual_invoice_succeeded()` :3812 |
| `license_service.py:2062` `change_license_plan()` | decorator | **yes** | `change_license_price()` -> `stripe_service.py:1582` |
| `views.py:726` `cancel()` | with | **yes** | `release_schedule()` -> `stripe_service.py:2078` |

### B1 — `handle_checkout_completed` — THE BOARD'S PRIORITY SITE, CONFIRMED

`@transaction.atomic` at **:2783** on `handle_checkout_completed` (:2784), which dispatches
to `_handle_individual_upgrade_checkout_completed` (:3318) at :2791. That helper carries
`release_schedule` (:3397), **`Subscription.modify` (:3399)** and the void/refund invoice
(:3406). So the board's originally-listed sites **are** inside a transaction — reached via
the decorator, which is why a lexical-only pass missed them. **The board was right that
these are defects; its line number (3442) and its implied location were both wrong.**

### B2 — `handle_invoice_payment_succeeded` (:3719, decorator, holds SFU)

Reaches `_handle_individual_invoice_succeeded` (:3812), the region carrying the `sync_price`
calls (:3927, :3991) — the board's sites 4. Path is unambiguous; **individual hand-confirm
still outstanding.**

### B3 — `change_license_plan` — NEW, NOT ON THE BOARD, NOT IN MY LEXICAL 7

`@transaction.atomic` at :2061 on `change_license_plan` (:2062) + `select_for_update`, which
calls `change_license_price()` at :2127 -> `stripe_service.py:1582`. That function does
**`Subscription.modify` at :1680** with a compensating revert at :1713, and the custom-price
path can additionally **`stripe.Price.create` (:2257)** — creating a Stripe object that a DB
rollback cannot remove. Same money shape as `update_seats`: "always_invoice for upgrades"
means **the school is charged**. **CONFIRMED sixth defect site.**

## CONFIRMED-DEFECT REGISTER (Phase 1, as it stands)

| # | Site | Layer | Why it matters |
|---|---|---|---|
| 1 | `license_service.py:3465` `convert_license_to_offline` | view/admin | **Worst.** Irreversible `Subscription.delete`; school silently unbilled |
| 2 | `license_service.py:2224`/`:2249` `update_seats` | view | School charged; compensator inside the doomed transaction |
| 3 | `license_service.py:2062` -> `stripe_service.py:1680` `change_license_plan` | view | School charged; may also create an orphan Stripe Price |
| 4 | `license_service.py:1969` `cancel_license_subscription` | view | Stripe stops renewing, app says it will |
| 5 | `views.py:754`/`:766` `cancel` | view | Two irreversible calls under one atomic + SFU |
| 6 | `stripe_service.py:3397`/`:3399`/`:3406` via `handle_checkout_completed` | webhook | The board's priority site; redeliverable, unlike 1-5 |

| 7 | `sync_price` `:1813` via `handle_invoice_payment_succeeded` `:3720` | webhook | Next cycle billed at the new price while the app holds the old plan — **but already idempotent** |

Not defects: `stripe_service.py:1054` (reference pattern), `:4691` (low/benign).

### B2 hand-confirm — COMPLETE (site 7)

`@transaction.atomic` at **:3718** on `handle_invoice_payment_succeeded` (:3720), reaching
`_handle_individual_invoice_succeeded` (:3812), which calls `sync_price` at :3927 and :3991.
`sync_price` (defined :1795) performs **`stripe.Subscription.modify` at :1813**. Confirmed
inside a transaction.

**Severity is genuinely lower, and the reason matters for the design:** `sync_price` is
**already idempotent by construction** — it retrieves the subscription, **returns early if
the price already matches** (:1806-1810), and only then modifies. After a rollback plus a
Stripe redelivery it converges on the correct state.

**The webhook recovery path is real and verified, not assumed:** the claim in
`webhooks.py:150-200` is **deliberately NOT wrapped in `transaction.atomic`** (stated in its
own docstring), so it **survives the handler's rollback**; `FAILED` and stale `PROCESSING`
claims are re-claimable; and Stripe redelivers for ~3 days (`STRIPE_RETRY_WINDOW`).

**So the asymmetry is now confirmed on BOTH sides**: the webhook layer has designed
recovery, the view layer has none. That is the argument in `DESIGN_PROPOSAL.md`.

**Five of the six are VIEW-layer — never redelivered.** Only #6 is a webhook. That ratio is
the argument against one uniform remedy.

## C. Headline: the board's 6 sites were the wrong 6

The board listed 6 sites, **all in `billing/stripe_service.py`**. The audit finds
only **2 of the 7 lexical hits are in that file** (A2, A3). **Four are in
`billing/license_service.py`** — school-licence cancellation, seat changes and
offline conversion — **a file that appears nowhere in the original audit**, and
one is in `billing/views.py`.

School-licence flows move **seat counts and money for whole schools**, so the
blast radius of A4-A7 is plausibly larger per incident than the individual
flows originally listed. **To be confirmed, not yet asserted.**

This is why d4's ruling to re-derive the class rather than work the list was
correct, and it means the original P1b audit was not merely incomplete at the
edges — it was pointed at the wrong file.

## Open questions for Phase 1

1. Hand-confirm each of the 7 lexical sites: the real transaction boundary
   (savepoint vs outermost), what DB writes are lost on rollback, and what
   Stripe keeps.
2. Triage section B; discard collisions, confirm the rest.
3. Does an existing mechanism already cover any site — the webhook claim
   (`webhooks.py:87-93`), `replay_stripe_events`, `price_reconciliation.py`,
   or e2's approved P1c ALLOW-1 sweep?
4. A3 (`Customer.modify`) already sits in its own `try/except StripeError` that
   logs and returns — it may be a different severity. Do not assume the 7 are
   one bug.
5. Gate-5 injection matrix per site (H6): before the call, during (timeout),
   after success/before commit, and on retry/redelivery — recording BOTH app-DB
   and Stripe state.

## What this file is NOT

No gate has been run. No fix is designed or written. The 7 lexical sites are
**tool output pending hand-confirmation**, not confirmed defects. Per H11.3
nothing here is "found", "fixed" or "secure".
