# FINDING — licence payment-failure paths diverge on ORDINARY customer behaviour

**Found 2026-09-18 while studying Change 1 before writing code. Confirmed by reading
`b744c9f`; NOT reproduced by a test; NOT fixed.** Raised to fixes-coordinator (95) and the
Senior Manager (d4) as a scope question before any implementation.

## Why this matters more than the timeout scenario

Everything in H-28 so far assumed P1b fires on a **rare** event: a 60 s Postgres kill, a
gunicorn timeout, a crash. The paths below diverge Stripe from the app on **ordinary,
common customer behaviour** — a card that needs 3D Secure, or a declined card — with no
timeout or crash involved. They are therefore **far more likely to have already fired in
production** than anything else in the H-28 register.

## The evidence: the individual path was fixed; the licence path was not

The individual-teacher upgrade path (`stripe_service.py:1150-1215`) handles payment failure
defensively, and its own comments record why:

- **CardError raised by `Subscription.modify`** -> calls `_revert_to_previous_price(...,
  invoice=None)` before raising. Its comment: *"Stripe applies the subscription item change
  as part of the same call that attempts payment, so the item swap may already be live on
  Stripe's side even though this raised — best-effort revert it back."*
- **Unpaid invoice (declined, requires 3DS, etc.)** -> calls `_revert_to_previous_price(...,
  invoice)` **first**, which reverts the price **and voids the open invoice** — its docstring
  (:1753-1755): *"voids the unpaid invoice so it doesn't linger as a dangling charge attempt"*.

The licence path does none of this consistently:

| # | Site | Trigger | What the code does | Result |
|---|---|---|---|---|
| F1 | `change_license_price` :1707-1711 | invoice unpaid, PaymentIntent `requires_action` (**3D Secure**) | raises `ValueError` **with no revert** | new price stays live at Stripe; `change_license_plan`'s atomic rolls the local plan back. **Deterministic P1b divergence on any 3DS card.** |
| F2 | `change_license_price` :1685-1686 | `CardError` from `Subscription.modify` | raises `ValueError` **with no revert** | per the individual path's own comment, the swap may already be live — divergence |
| F3 | `change_license_price` :1713-1717 | invoice unpaid, other reason | inline revert of the **price only**; the open invoice is **not voided** | a dangling open invoice Stripe may still try to collect, for a change the app reports as *"Plan has not been changed."* |
| F4 | `update_seats` :2246-2257 | invoice unpaid (incl. 3DS) | inline revert of the **quantity only**; invoice **not voided**; no `requires_action` distinction | same dangling-charge shape as F3, for seats the app reports as not increased |
| F5 | `update_seats` :2273 | `CardError` from `Subscription.modify` | caught as `StripeError`, raises, **no revert** | same as F2 |

F1, F2 and F5 are **exactly the P1b class** — an irreversible Stripe mutation left applied
while the local transaction rolls back — on Change-1 sites. F3 and F4 are adjacent: a
compensation that is present but incomplete.

**No licence-path test covers `requires_action`.** The only test mentioning it is
`billing/tests/test_subscription_upgrade.py`, which exercises the individual path.

## What I am NOT asserting

I am not asserting Stripe's collection behaviour beyond what this codebase's own comments and
docstrings already state. F2/F5 depend on the individual path's documented belief that a
swap may be live after a `CardError`; neither path passes `payment_behavior`, so the common
failure surfaces via the invoice status (F1, F3, F4) rather than an exception.

## Proposed handling — for d4's ruling

The in-tree fix already exists: `_revert_to_previous_price` (`stripe_service.py:1749`), used
by the individual path. Pattern 2a's compensation step for Change 1 would call it, bringing
the licence path into line with the individual path. That is **consistent with the approved
design's philosophy** (use in-tree precedent, invent nothing).

It is nevertheless a **customer-visible behaviour change** on F1: a 3DS upgrade is reverted
and the invoice voided, rather than left live. That matches the existing error text
(*"Please update your payment method and retry"*) but it is still a change, so under the
doctrine it needs the 4-point behaviour-change record and a decision rather than being folded
in silently.

**Question for d4:** include F1-F5 in Change 1 (they sit in the exact functions being
restructured), or split them into their own change?

## Consequence for the detector spec

`SPEC_audit_stripe_divergence.md` compares subscription **status** only. F1-F5 leave status
`active` on both sides but the **price or quantity** disagreeing. The detector should also
compare **Stripe's current price and quantity** against the app's plan / `custom_price_cents`
/ `max_seats`, and list **open invoices** on licence subscriptions. Without that, the most
likely production divergence is invisible to it.
