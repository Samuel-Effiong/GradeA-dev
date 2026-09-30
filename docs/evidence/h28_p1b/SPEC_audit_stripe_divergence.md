# SPEC — `audit_stripe_divergence` management command

**Status: SPEC ONLY. NOT BUILT. Build-ready the moment production read access is granted.**
Approved as the spec by the Senior Manager (d4), 2026-09-18, adopting all six corrections
raised by fix-p1b. H-28 (P1b) companion deliverable.

## Purpose — dual

1. **The P1b detector.** Answer "has `convert_license_to_offline()` (or a sibling site) ever
   actually fired in production and left Stripe and the app disagreeing?"
2. **A permanent Stripe-reconciliation safety net**, worth having regardless of H-28. Any
   future divergence from any cause surfaces here.

## Why this is the only reliable detector

When one of the confirmed sites dies after its Stripe call, the DB transaction rolls **all
the way back**. The row returns to `billing_method=STRIPE` with `stripe_subscription_id`
still populated — **byte-identical to a healthy licence**. There is no local shape to search
for. The divergence exists only as a disagreement between two systems, so only a
cross-system comparison can find it.

Good news: **the evidence does not decay.** `Subscription.delete()` does not erase anything
at Stripe — the object persists, retrievable, with `status=canceled`, indefinitely. A check
run months later is as good as one run today.

## What it is NOT

**It detects DIVERGENCE, which is a SUPERSET of P1b.** Innocent causes of "app says active,
Stripe says canceled" include a `cancel_at_period_end` maturing normally, dunning/payment
failure, a manual cancel in the Stripe dashboard, and a missed renewal webhook.
**The raw row count must never be reported as "N schools hit by P1b."** Every row triages.

## The P1b discriminator

Two conditions **together**; neither is sufficient alone.

1. **Immediate cancellation at Stripe** — `cancel_at_period_end == false` and
   `canceled_at == ended_at`. Decisive because `convert_license_to_offline()` is the only
   path in the codebase that calls `Subscription.delete()` (immediate). Every legitimate
   cancellation path — `cancel_license_subscription()` (`license_service.py:1969`) and
   `views.py` `cancel()` (`:766`) — sets `cancel_at_period_end=True`. Both verified by
   reading, 2026-09-18.
2. **No local record explains it** — no `LicenseBillingRecord` of type `CANCELLED` or
   `CONVERTED_TO_OFFLINE` — because the rollback destroyed that row.

Also capture `cancellation_details.reason` to separate `payment_failed` from
`cancellation_requested`.

## Scope — BOTH models

| Model | Path | Population |
|---|---|---|
| `LicenseSubscription` | schools | fewer, higher value each |
| `UserSubscription` | individual teachers (`views.py:766`) | **larger** |

A schools-only check misses most of the exposure.

## Enumeration — by CUSTOMER, not by stored subscription id

Use `stripe.Subscription.list(customer=stripe_customer_id)` alongside retrieving the stored
id. Reason: a later **successful** conversion NULLs the local `stripe_subscription_id`,
which would hide an earlier orphaned subscription. `stripe_customer_id` is **never cleared**
by the conversion (`license_service.py:3471-3481`), so it is the durable handle.

## Both directions

| Direction | Meaning | Urgency |
|---|---|---|
| App says ACTIVE / Stripe says canceled | revenue we stopped collecting | high |
| **App says OFFLINE / Stripe still ACTIVE** | **a customer still being charged by card while we believe we are invoicing them** | **arguably higher — money leaving a customer wrongly** |

The mirror direction is nearly free once enumeration is by customer, and the original
framing omitted it.

## Logs are a SUPPLEMENT ONLY

Log scanning for `convert_license_to_offline()` exceptions is worth doing but must not be
the primary detector:
- the P1b case is the Stripe call **succeeding** and the transaction dying afterwards;
- a Postgres transaction kill raises `OperationalError`, which probably logs — but a
  **gunicorn hard-kill of the worker mid-request may leave no log line at all**;
- Railway log retention is finite, so an older incident leaves nothing.

Stripe's retained subscription object is primary. Logs corroborate.

## Binding properties — non-negotiable (d4)

1. **Reads only** — `retrieve` / `list`. Never `modify`, `delete`, `create`, `cancel`.
2. **Rate-limited.**
3. **Reports rows. Writes NOTHING to Stripe. Auto-repairs NOTHING.**
   Repair is a **per-customer human decision** — an auto-repair here could re-create or
   re-cancel a subscription and make real money move.
4. Inherits `billing/price_reconciliation.py`'s stated doctrine: **Stripe -> application
   always; application -> Stripe never, under any circumstance.**

## Shape

A committed management command, matching the existing house pattern
(`billing/management/commands/audit_school_admins.py`,
`audit_email_track_separation.py`) — not an ad-hoc script, so it is reviewable, rerunnable
and gatable.

Suggested output per row: model, local id, school/user, local `billing_method` +
`is_active`, Stripe subscription id + `status` + `cancel_at_period_end` + `canceled_at` +
`ended_at` + `cancellation_details.reason`, whether a local `LicenseBillingRecord` explains
it, and a **verdict column**: `P1B_FINGERPRINT` / `DIVERGENT_OTHER_CAUSE` / `MIRROR_ACTIVE`.

## Cost line for the user

> A read-only audit command over both subscription models, one to two Stripe reads per
> customer, rate-limited. It needs **production DB read access** and a **production Stripe
> READ key**. It writes nothing and repairs nothing. **It cannot be run from here today** —
> there is no production DB credential in this environment.

## Verification, when built

Not exempt from the doctrine. Gate 2 mutants on the discriminator logic (flip
`cancel_at_period_end`, drop the "no local record" condition, invert the mirror direction);
Gate 5 for Stripe timeout/error mid-enumeration; Gate 7 against Stripe test mode with
fixtures for each verdict class. Gate-8 class: environment-sensitive.
