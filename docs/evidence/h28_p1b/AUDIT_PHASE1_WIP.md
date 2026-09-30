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

## B. Transitive candidates — atomic wrapping a mutation across a call boundary

Over-inclusive; `list()` / `create()` rows are probable name collisions.
**Not yet triaged.**

| Site | SFU | Reaches |
|---|---|---|
| `billing/views.py:726` `cancel()` | **yes** | `release_schedule()` -> `stripe_service.py:2078` |
| `billing/tasks.py:269` `process_license_renewals()` | **yes** | `process_license_renewal()` -> `license_service.py:1685` |
| `billing/tasks.py:526` `reconcile_subscription_renewals()` | **yes** | `process_rollover_and_renewal()` -> `services.py:709` |
| `billing/tasks.py:996` `process_license_monthly_credit_refreshes()` | **yes** | `_refresh_teacher_credits()` -> `license_service.py:3236` |
| `billing/license_views.py:428` `process_renewal()` | no | `process_license_renewal()` |
| `billing/license_views.py:324` `add_teachers()` | no | `add_teachers_batch()` -> `license_service.py:1549` |
| `billing/license_views.py:248` `create()` | no | `create()` — likely collision |
| `billing/license_service.py:1761` `process_license_renewal()` | no | `_rollover_and_grant_monthly_bucket()` |
| `billing/license_service.py:3357` `process_offline_renewal()` | no | `_rollover_and_grant_monthly_bucket()` |
| `billing/services.py:1224` `refund_credits()` | **yes** | `list()` — likely collision |
| `billing/views.py:2450` `custom_ai_prompt()` | no | `append_dashboard_chat_message()` — likely collision |

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
