# Billing hardening record

Permanent record of the section 2 (`billing`) hardening programme: what
was found, how it was proven, and what remains open.

This is deliberately not a changelog. A changelog says what changed; this
says **what was wrong, how we know it is now right, and what would tell us
if it broke again**. Evidence that lives only in a terminal scrollback is
evidence we do not have.

**Status at close of Item 5:** Items 1, 2 and 5 complete. Items 3 and 4
(account-2 configuration) tracked and deliberately untouched.

---

## The standard applied

> A customer can purchase overage credits individually or through a school
> licence, and regardless of duplication, concurrency, retries, failures,
> refunds, webhook ordering or partial consumption, the resulting money,
> wallet, ledger and entitlement state remain correct and auditable.

Every claim below is backed by a test, a mutation that proves the test
bites, or a real Stripe transaction. Where something is unproven, it says
so.

---

## Defects found and fixed

Nine confirmed production defects. Six were found by testing that went
beyond the original scope — they were not on anyone's list.

| # | Defect | How it presented | Found by |
|---|---|---|---|
| 1 | **Silent double-charge.** `invoice.payment_intent` was removed in the pinned Stripe API version, so the compensating refund for a duplicate interval-change invoice never fired. | Monthly→annual upgrade charged twice; no refund. | Real Stripe run |
| 2 | **Scheduled downgrades never applied.** Schedule phase transitions bill with `billing_reason="subscription_update"`, which was not in `RENEWAL_BILLING_REASONS`. | Customer paid the new price, received no credits, stayed on the old plan. | Real Stripe run |
| 3 | **98% webhook loss during bursts.** Slow handlers held the HTTP request open. | Burst hours captured 2% of `invoice.payment_succeeded`; quiet hours 100%. | Deployed-DB measurement |
| 4 | **Overage double-grant.** A duplicate `checkout.session.completed` granted a second block. | 1,000 credits for a 500-credit payment. | Probe during Item 5 |
| 5 | **Overage granted on unconfirmed payment.** No `payment_status` check on the individual flow. | Credits for a payment not yet made. | Item 5 review |
| 6 | **Refund kept the credits.** `charge.refunded` recorded the money and stopped. | Every cent back, every credit kept — repeatable for unlimited free credits. | Item 5 testing |
| 7 | **Chargeback on an overage purchase reclaimed nothing.** The dispute path only understood subscription payments. | Chargeback won by the customer, credits retained. | Refund work |
| 8 | **Reversal deadlocked against a concurrent spend.** Lock order inverted relative to `consume_credits`. | `deadlock detected ... billing_creditwallet`. | Real-thread test |
| 9 | **A won chargeback left school teachers permanently blocked.** Debt was cleared only from the transaction's owner. | Teachers unable to spend, forever. | Dispute/refund integration |

Plus three defects introduced **during** this work and caught before
release — recorded because a hardening record that only lists other
people's mistakes is not honest:

| # | Defect | Caught by |
|---|---|---|
| A | Concurrent QA poller dispatched without `inline=True`, so every event was claimed and never run. | The `event.no_stuck_processing` invariant |
| B | Broker outage during claim recovery settled the row `FAILED`, stranding exactly the events recovery exists to save. | Its own test suite |
| C | `reconcile_overage_prices` reported "every plan is in sync" while some plans could not be read at all. | A transient DNS failure during a live run |

---

## The three findings closed before final verification

### 1. Overage price drift (P1)

**Both purchase flows priced their Stripe session from
`stripe_overage_price_id` while every local number — quote, purchase
intent, offline request, log line — came from `plan.overage_block_price`.
Nothing made them agree.**

Measured against real Stripe, **six of nine** plans had drifted:

| plan | quoted | charged | Stripe price nickname |
|---|---|---|---|
| STANDARD | 500 | 500 | Standard Overage |
| STANDARD_ANNUAL | 500 | 500 | Standard Overage |
| PRO_ANNUAL | 400 | 400 | Pro Overage |
| PRO | 500 | **400** | Pro Overage |
| POWER | 500 | **300** | Power Overage |
| POWER_ANNUAL | 3 | **300** | Power Overage |
| BETA | 0 | **500** | Standard Overage |
| PRO_LICENSE | 299 | **400** | Pro Overage |
| POWER_LICENSE | 299 | **300** | Power Overage |

A school buying three PRO_LICENSE blocks was quoted **897** and charged
**1200**.

**Source of truth: Stripe**, and not by preference — by mechanism. Stripe
is what moves the money; a local column cannot be authoritative about a
charge it does not make. The Stripe side is also demonstrably deliberate:
the prices are nicknamed per tier in a coherent descending volume
discount, and the three plans that agree with it sit exactly where that
scheme predicts. The drifted rows read as never updated when tier pricing
landed.

**Resolution**

- `billing/overage_pricing.py` — refuses to quote a price we will not
  charge, on both flows, including the offline quote. An unreachable
  Stripe is treated as a refusal, never as agreement.
- `reconcile_overage_prices` Celery task — nightly detection, so drift is
  found before a customer meets the refusal.
- `manage.py reconcile_overage_prices [--fix]` — human-run correction.
  Deliberately a command, not a migration: a migration runs blind against
  databases this code has never inspected, and this is a money-facing
  column.
- Local development database aligned (9/9 in sync). **Aligning the column
  to Stripe changes nobody's actual charge** — Stripe was already charging
  these amounts. It only makes the quoted and recorded figure truthful.

**Open, for a product decision:** PRO_LICENSE and POWER_LICENSE both
carried a flat 299 while pointing at the *individual* tier's Stripe
prices, because no school-specific Stripe price exists. That is a pricing
intent with nowhere to live, not a typo. Aligning them to 400/300 makes
today's quote honest; the real fix is a school overage price in the
deployed account — **which is Items 3/4, deliberately not done here.**

### 2. Abandoned `PROCESSING` claim recovery (P1)

**Item 1 removed a safety net without anyone noticing.**

The claim ledger always had stale-claim stealing, and the hourly sweeper
always marked abandoned claims `FAILED` — a *claimable* state. That was
sufficient while handlers ran inside the HTTP request: a dead worker meant
no 2xx, so Stripe redelivered and the redelivery re-claimed the row.

Asynchronous dispatch changed that. The endpoint now claims and answers
**200 in milliseconds**, so Stripe considers the delivery successful and
will never send it again. If the Celery worker then dies, the row is
claimable in principle with **no claimant left in the world**.

```
before:  worker dies -> Stripe redelivers    -> recovered
after:   worker dies -> Stripe already got 200 -> stranded
```

**The discriminator.** Re-running a handler is not unconditionally safe —
they call `stripe.Refund.create` and `Subscription.modify`, which no
database rollback undoes. So recovery turns on one recorded fact:

| `handler_started_at` | meaning | action |
|---|---|---|
| `NULL` | worker died between claiming and doing anything; no Stripe call was made | **re-dispatch** |
| set | worker may have got part-way through irreversible calls | `FAILED`, for a human |

Hardened against every mode requested: worker dying before processing;
during processing; a slow worker mistaken for dead; two sweepers racing; a
zombie worker completing after its claim was reclaimed; repeated reaping
(capped at `STRIPE_EVENT_MAX_RECOVERY_ATTEMPTS`); and duplicate delivery
mid-recovery. The existing fencing token remains effective throughout — a
recovered claim cannot be overwritten by the original worker.

### 3. Reconciliation classification (P2)

Our own compensating refunds — for duplicate invoices deliberately never
recorded as customer charges — were filed as unexplained money movement.
Observed twice in a single live-QA run.

Fixed by classification, not suppression: the refund is stamped at issue
time, recognised on the way back in, and recorded quietly with a
queryable reason. **Genuinely unexplained refunds still shout** — that
half is tested explicitly, because the easy way to fix noise is to silence
the alarm, which trades a nuisance for a blind spot.

---

## The refund rule (accepted)

Stated in full in `billing/payment_refunds.py`, which is the authority.

> **Financial records and customer entitlement must never silently diverge
> from the final payment state.**

- Credits bought by a refunded payment are reclaimed **proportionally**,
  floored in the customer's favour.
- Deliberate retention is supported but must be **asked for**
  (`retain_credits` in Stripe metadata) and is recorded on the
  `PaymentRefund` row. It never happens by accident.
- Already-spent credits become a **deficit** that blocks further
  consumption. Usage history is never rewritten — the work really happened
  and really cost us.
- Every quantity is a **cumulative target**, never an increment. That is
  what makes duplicates inert, out-of-order deliveries harmless and
  retries convergent.
- Refunds and chargebacks share one settlement view
  (`billing/credit_reversal.py`), so the same credits cannot be reversed
  twice — once by each cause.

**Out of scope, deliberately:** refunds of *subscription* invoices remain
money-only. Reclaiming a cycle's allocation on a partial refund is
entangled with plan changes and prorations; inventing a rule there would
be guesswork.

---

## Evidence

### Mutation testing — 25 mutations, 25 caught

Every protection was reverted and the suite had to fail. The tree was
verified byte-identical after each battery.

| Battery | Result |
|---|---|
| Refund / reversal (`mutate_refund`) | **15 / 15** |
| Overage price guard (`mutate_price`) | **8 / 8** |
| Abandoned-claim recovery (`mutate_reaper`) | **10 / 10** |
| Refund classification (`mutate_class`) | **7 / 7** |

Four mutations initially **escaped**, and each exposed a real gap in the
tests rather than a safe protection:

- The reversal ceiling was unreachable through the refund path (the model
  property clamps first) — tested at the engine's own level instead.
- Cross-wallet theft needs a **partial** refund to be visible; a full
  refund leaves no spare to steal.
- The proportional split was only ever exercised on even divisions.
- The compensating refund's marker was only tested on the *reader* side;
  nothing proved it was written.

One further mutation was an **equivalent mutant** — dropping the explicit
`stripe_payment_intent_id` kwarg changes nothing, because
`CreditLedger.build` promotes it out of metadata. It was re-expressed
against the protection that actually carries the attribution.

### Real Stripe (test mode)

- Fast tier: **20 / 21** scenarios; the sole failure was the price drift,
  a genuine data finding, now resolved.
- `license_overage_purchase`: **31 / 31** checks after the price fix.
- `license_overage_isolation_and_refund`: **16 / 16**, including a real
  `stripe.Refund.create` clawing back a real block, and its redelivery
  proving idempotency.
- Concurrent dispatch after the `inline` fix: **3 / 3**, no stuck claims.

### Suites

`billing` — see the final verification section. Refund lifecycle: 61
tests across 11 classes. Overage integrity: 38. Disputes: 24. Price drift:
17. Claim recovery: 20. Classification: 15.

Concurrency is exercised with **real threads against real PostgreSQL**,
barrier-synchronised — not simulated.

---

## Still open

| Item | Priority | Note |
|---|---|---|
| Item 3 — subscribe `setup_intent.succeeded`; school overage price in account 2 | P1 | Blocked on rotated credentials. **Tracked, untouched.** |
| Item 4 — re-verify against the deployed `2025-12-15.clover` API version | P1 | Same. **Tracked, untouched.** |
| School overage pricing intent (flat 299 vs per-tier) | P1 | Product decision; depends on Item 3. |
| Deployed database price alignment | P1 | Run `reconcile_overage_prices` there deliberately. Local only so far. |
| F9 handler transaction design | P2 | Mitigated by async dispatch; the transaction shape is unchanged. |
| `invoice_payment.paid` | P2 | Explicitly left alone by owner instruction. |
| Webhook subscription trimming (236 → 9) | P2 | Do not trim before `invoice_payment.paid` is understood. |
| `StripeEvent` storage growth | P2 | Largest billing table; ~9.5 GB/yr at 10k events/day. |
| Subscription-invoice refunds | P2 | Money-only by design; see above. |
| `carry_over_and_max_bank` QA scenario | P3 | Pre-existing `select_for_update` outside a transaction — the scenario has been erroring, not testing. |
| Concurrent live-QA clock | P3 | `local_clock()` patches `timezone.now` globally; not thread-safe, so `--workers > 1` cannot use it. |

---

## Operational notes

- The live-QA fast tier **must** be invoked as `--tier fast`. A bare
  invocation runs *every* scenario including the multi-year horizon runs,
  which take hours.
- `--workers > 1` routes events through a shared poller and finishes far
  faster, but cannot use the simulated clock; treat clock-sensitive
  results from concurrent runs with care.
- After any mutation testing, **verify the tree by checksum**. A restored
  mutant has twice survived into the working tree during this programme.
