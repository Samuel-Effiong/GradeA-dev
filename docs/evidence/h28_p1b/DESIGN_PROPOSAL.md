# H-28 (P1b) — per-flow design proposal

**Status: PROPOSAL. NO CODE WRITTEN. Requires the Senior Manager's approval before any
implementation.** Routed fix-p1b -> fixes-coordinator (95) -> Senior Manager (d4).
Tree: `b744c9f`. Gate-8 class: environment-sensitive / billing.

---

## 1. The finding that shapes everything: the asymmetry

**The seven confirmed sites are NOT equally dangerous, and must NOT get one remedy.**
Five are view-layer, two are webhook.

| | Webhook layer | View layer |
|---|---|---|
| Sites | 2 of 7 | **5 of 7** |
| How it dies | Postgres `idle_in_transaction_session_timeout` = **60 s**, shorter than stripe-python's **80 s** default, so the transaction can be terminated *mid-call* | gunicorn request timeout, client disconnect, or any later exception |
| Is there a second chance? | **YES** | **NO** |
| Recovery machinery | Verified in-tree: the claim in `webhooks.py:150-200` is **deliberately not atomic**, so it survives the rollback; `FAILED` and stale `PROCESSING` are re-claimable; Stripe redelivers for ~3 days (`STRIPE_RETRY_WINDOW`) | **None. Nothing retries a dead web request.** |
| Therefore | divergence is **transient and self-healing**, given idempotent handlers | divergence is **silent and permanent** |

**This is the whole argument.** A remedy adequate at the webhook layer is inadequate at the
view layer, because the webhook layer already has a designed recovery path and the view
layer has nothing at all.

## 2. Both remedies already exist in this codebase

Neither half of this proposal invents an architecture. Both cite working in-tree precedent.

### 2a. View-layer remedy — `stripe_service.py:975-1070` (`reactivate_if_cancelling`)

Originally flagged by my own tool as a defect; reading it showed it is the **reference
pattern**, and I reclassified it:

1. the irreversible Stripe mutation runs **OUTSIDE** the transaction (~:998);
2. the `atomic` block (:1010) is **short and purely local**;
3. on local-save failure, a **compensating revert** (:1054);
4. when the compensation *itself* fails, a loud
   **`MANUAL RECONCILIATION NEEDED — Stripe and local state now disagree`** log.

### 2b. Webhook-layer remedy — idempotency + redelivery, already partly present

`sync_price` (`stripe_service.py:1795-1822`) is **already idempotent by construction**: it
retrieves the subscription, returns early if the price already matches, and only then
modifies. Re-running it after a redelivery converges on the correct state. That is the
webhook remedy working today.

## 3. Ordering, not speed — and the revert must be able to fail

Two principles, the first from e2's P1 mechanism work:

1. **Ordering is what saves you.** Any call inside the transaction can have that transaction
   terminated mid-call at 60 s while the call itself is still allowed 80 s. **Shortening the
   Stripe timeout narrows the window; it does not close it. Only moving the call out does.**
   No proposal here relies on a timeout tweak.
2. **A revert that fails silently is worse than no revert**, because it converts a loud
   divergence into a quiet one. The loud log in 2a is not decoration — it is the half that
   makes the pattern safe. Note `update_seats` (:2249) has a revert that is **itself inside
   the doomed transaction**, so it is skipped entirely by a mid-call kill: actively
   misleading to a reader who sees a revert and assumes coverage.

## 4. Per-flow proposal

Priority is blast radius per incident × irreversibility × absence of recovery.

| P | Site | Layer | What goes wrong | Proposed remedy |
|---|---|---|---|---|
| **P0** | `license_service.py:3465` `convert_license_to_offline` | view | `Subscription.delete` is **unrecoverable** — no un-delete. Rollback leaves the school's subscription gone while the app still bills it as STRIPE | **Pattern 2a**, plus a **state-machine intent row** (`PENDING` -> `STRIPE_DELETED` -> `COMPLETE`, terminal `FAILED`; see §8b.3) committed **before** the Stripe call, with `STRIPE_DELETED` committed **immediately after** it returns. Compensation is impossible here, so the intent row and the loud log ARE the safety net. Stripe `idempotency_key` from the intent row id |
| **P1** | `license_service.py:2062` -> `stripe_service.py:1680` `change_license_plan` | view | school charged (`always_invoice`); custom-price path can orphan a `stripe.Price` (:2257) | **Pattern 2a.** Move `Price.create` out too — an orphan Price is harmless but should be recorded |
| **P1** | `license_service.py:2224`/`:2249` `update_seats` | view | school charged; **revert at :2249 is inside the doomed transaction** | **Pattern 2a**, and move the revert OUT so it can actually run |
| **P1** | `license_service.py:1969` `cancel_license_subscription` | view | Stripe stops renewing; app says it will renew | **Pattern 2a** |
| **P2** | `views.py:754`/`:766` `cancel` | view | two irreversible calls under one `atomic` + `select_for_update`; lock held across both | **Pattern 2a**; release the lock before the Stripe calls. Individual population is larger, value per incident smaller |
| **P1** *(was P3 — re-ranked in review, see §8)* | `stripe_service.py:3397`/`:3399`/`:3406` via `handle_checkout_completed` | webhook | upgrade applied at Stripe, DB rolled back | **Pattern 2b** — establish handler-level idempotency **by test**, so redelivery provably converges. Ranked alongside the P1s **until that is proven**; recovery machinery only heals an idempotent handler |
| **P4** | `sync_price` `:1813` via `handle_invoice_payment_succeeded` | webhook | next cycle billed at new price, app on old plan | **Verify only.** Already idempotent (:1806-1810 early return). Add a regression test pinning that property so a future edit cannot remove it |

**Explicitly NOT changed:** `stripe_service.py:1054` (the reference pattern — changing it
would remove the model answer) and `:4691` `handle_setup_intent_succeeded` (nothing follows
it but logging; redelivery re-sets the same default payment method idempotently).

## 5. Gate-5 injection matrix (H6)

Per site, inject at each point and record **both** app-DB state and Stripe state:

| # | Injection point | Expected after fix |
|---|---|---|
| 1 | before the Stripe call | no Stripe change; local unchanged; clean error |
| 2 | during the call (timeout / 60 s transaction kill) | outcome intentional and recorded; no silent divergence |
| 3 | **after Stripe succeeds, before the local commit** | the P1b case. Compensation runs, or the loud log + intent row fires |
| 4 | on retry / redelivery | webhook: converges idempotently. View: no duplicate mutation |

**Runtime proof, not code-reading:** `assert_no_call_inside_transaction(test, fake,
only_receipt_lookups=False)` from e2's harness (`task/fake-stripe-harness` @ `5400759`)
records `in_transaction=connection.in_atomic_block` per call. That assertion is what turns
this audit into evidence and is the Gate-2 mutant target for every site.

## 6. Risks and open questions for d4

1. **P0 has no true compensation.** A deleted Stripe subscription cannot be restored, only
   re-created — a *new* subscription with a new id, new billing anchor, possibly a new
   charge. I am **not** proposing auto-recreation; that would move real money. The proposal
   is durable-intent + loud log + the reconciliation detector, and a **human** decides.
2. **Should the P0 flow be gated behind a confirmation or made async?** Out of scope for
   H-28 as a bug fix; flagging as a product question.
3. **Scope discipline.** Seven sites is already large. Proposed landing order, each its own
   bounded change rather than one sweeping diff:
   - **Change 1 — licence layer:** P0 + the three licence P1s, all in `license_service.py`.
   - **Change 2 — `handle_checkout_completed`** (the re-ranked webhook site, now P1 per §8a).
     Same priority as change 1 but a **different file and a different remedy class**
     (idempotency-by-test, not compensation), so it does not join the licence batch. It can
     proceed in parallel with change 1.
   - **Change 3 — P2** `views.py cancel`.
   - **Change 4 — P4** `sync_price`: a regression test pinning its idempotency; no behaviour change.
4. **The detector is the safety net, not the fix** — and it is blocked on prod read access.

## 8. Review round 1 — fixes-coordinator (95), 2026-09-18

### 8a. P3 re-ranked to P1 — conclusion ACCEPTED, stated mechanism CORRECTED

**Accepted:** my original P3 ranking said "lower priority because recovery machinery already
exists." That does not follow. **Redelivery only heals a handler that is idempotent**, and I
had not established that for this handler. e2, reviewing the same code independently for the
P1c sweep, **DENIED** auto-replay of `individual_upgrade_checkout` for exactly this reason
(`team/sessions/fix-overage-lock.md`, "Denied, with reasons"). An independent reviewer's
caution on the same path is evidence, and I should not rank below it on an assumption.

**Corrected, because d4 should decide on the right mechanism:** the review predicted that on
redelivery the void/refund step would error at Stripe, drive the claim to `FAILED`, and retry
without converging for ~3 days. **Reading `_void_or_refund_side_effect_invoice`
(`stripe_service.py:1490-1580`) shows that step does NOT behave that way:**
- it **re-reads state first** — retrieves the subscription, its `latest_invoice`, and that
  invoice's status — before acting;
- `open` -> void; once voided, a re-run sees a non-`open` status and **does nothing**;
- `paid` -> `Refund.create` **with `idempotency_key=f"interval-change-refund-{pi_id}"`**, so
  Stripe itself refuses a second refund;
- `StripeError` is **caught and logged, not raised** (it logs MANUAL RECONCILIATION NEEDED),
  so it **cannot** drive the claim to `FAILED`.

So the void/refund step is idempotent, verified by reading. **What is NOT established is the
main `Subscription.modify` (:3399) plus the interval-crossing anchor reset** converging on a
re-run. My expectation is that re-applying the same price is a no-op at Stripe and the second
run finds the same side-effect invoice already neutralised — but that rests on Stripe API
semantics I am **inferring, not testing**. Inferred is not proven, which is exactly why the
ranking moves up until a test settles it.

**P4 note, same logic:** e2 also denied `invoice.payment_succeeded` ("reaches `sync_price`, a
Stripe mutation"). My claim is narrower than "the handler is safe": the **Stripe call** in
`sync_price` is idempotent (early return, :1806-1810), which is what matters for Stripe's own
redelivery after a *full* rollback. e2's sweep replays events **long after the fact**, when
local state may have moved on — a different risk. Both positions hold; they answer different
questions. P4 stays verify-only for H-28.

### 8b. Three design conditions — ALL ACCEPTED

1. **Stripe `idempotency_key` on every view-layer mutation, derived from the intent row id.**
   Accepted. My injection point 4 promised "no duplicate mutation" with nothing enforcing it;
   a double-clicked Cancel, or a retry after a gunicorn timeout, issues a second
   `Subscription.modify`. **This also has in-tree precedent** — the very helper above already
   uses `interval-change-refund-{pi_id}` — so it stays consistent with house style.
2. **P2's lock release needs a named replacement guard.** Accepted. Removing
   `select_for_update` without a replacement trades divergence for a concurrent-cancel race.
   Named: **(a) locally**, the intent row carries a **unique constraint on (subscription,
   operation) while in a non-terminal state**, so a second concurrent cancel fails to insert
   and never reaches Stripe; **(b) at Stripe**, the idempotency key from condition 1. Both, not
   either — (a) stops the local race, (b) stops a duplicate that slips past it.
3. **P0's intent row needs explicit states.** Accepted, and this is the sharpest point in the
   review. Proposed: **`PENDING` -> `STRIPE_DELETED` -> `COMPLETE`**, plus terminal `FAILED`.
   **"Never attempted" and "deleted at Stripe but never finalised" need opposite responses** —
   the first is safe to retry, the second must NOT be retried and needs a human — and a single
   existence flag cannot tell them apart. On P0, where no compensation exists, the intent row
   **is** the safety net, so its precision is the whole design. The `STRIPE_DELETED` write must
   commit **immediately after** the Stripe call returns, in its own short transaction, before
   any other local work.

### 8c. Typo fixed

§1 said "Six of the seven". Leftover from before site 7. Now: five view-layer, two webhook.

## 9. Change 1 — compensation-step design (for d4's H11.1 read)

Approved so far (d4, 2026-09-18, via 95): the direction; Change 1 = P0 + the three licence
P1s **+ F1-F5** (`FINDING_licence_payment_failure_paths.md`); F1's behaviour change in
principle, conditional on the 4-point record; the money-aware branching rule; the bounded
retry. **Items marked NEW below have not yet been seen by d4.**

### 9a. Four phases per operation

| Phase | Transaction | Does |
|---|---|---|
| A | short, `atomic(durable=True)` | lock licence, validate, INSERT intent `PENDING` (the per-licence guard fires here), COMMIT — **lock released before any network call** |
| B | **none** | the Stripe call(s), each with its own `idempotency_key` |
| C | short, `atomic(durable=True)` | intent -> `STRIPE_APPLIED`, COMMIT **immediately** — before any other local work |
| D | short, `atomic(durable=True)`, retried per 9e | re-lock, re-validate, apply local writes, intent -> `COMPLETE`, COMMIT; then MailerLite sync **after** the commit |

**NEW — `durable=True` as enforcement.** Django 5.2's `atomic(durable=True)` raises
`RuntimeError` if it is ever opened inside another transaction. I checked every production
caller (`license_views.py:650/852/886`, `select_plan` at `license_service.py:3604` via
`license_views.py:595`): none wraps these functions in an outer transaction today, and
`ATOMIC_REQUESTS` is enforced off. `durable=True` makes that **a guarantee rather than an
observation** — a future caller that wraps one of these in a transaction fails loudly
instead of silently putting the Stripe call back inside a transaction. Django exempts the
test-case wrapper, so existing `TestCase` tests keep working. No precedent in-tree yet.

### 9b. The per-licence guard

A conditional `UniqueConstraint` on `license_subscription`, **across all operations**, while
the intent is non-terminal. Deviation from the design's "(subscription, operation)" wording,
endorsed by 95 and d4: the old `select_for_update` serialised every one of these functions on
the same row, so a per-operation guard would let a seat change and a conversion-to-offline
interleave where they never could before. Per-licence reproduces the old mutual exclusion
without holding a DB lock across the network. A blocked caller gets a `ValueError` (the
views' existing 400 contract): *another billing change for this licence is still in
progress or awaiting reconciliation.*

**Consequence, stated plainly:** a stuck non-terminal row blocks **all** further Stripe
changes on that licence until a human resolves it. For a licence whose Stripe state is
unknown, blocking further mutations is the safe failure. The cost is a support ticket.

### 9c. The full state set

| State | Terminal? | Blocks guard? | Meaning |
|---|---|---|---|
| `PENDING` | no | yes | recorded; Stripe not yet called, **or outcome unknown** if the process died during the call |
| `STRIPE_APPLIED` | no | yes | Stripe mutation succeeded; local not yet finalised |
| `COMPLETE` | **yes** | no | both sides agree, forward |
| `COMPENSATED` | **yes** | no | Stripe was reverted after a failure where no money moved; both sides agree, back where they started |
| `FAILED` | **yes** | no | Stripe never applied the change (rejected, or a payment failure reverted via F1-F5); local untouched |
| `ESCALATED` | no | yes | code gave up; a human has been alerted and must roll forward or resolve |

**Naming:** the approved P0 wording `STRIPE_DELETED` is `STRIPE_APPLIED` with
`operation=CONVERT_TO_OFFLINE`. One state for all operations keeps the detector's classes
uniform.

**NEW — `COMPENSATED` is a sixth state** beyond d4's list. "Applied at Stripe then
reverted" differs from "never applied" for audit: Stripe saw two mutations and possibly a
voided invoice. Folding it into `FAILED` would lose that.

**What a stale row means**, which is what the detector classifies on: stale `PENDING` =
outcome unknown, check Stripe. Stale `STRIPE_APPLIED` = Stripe done, local not, **and no
alert was sent** (see 9g). `ESCALATED` = alert sent, awaiting a human.

### 9d. The money-aware branching rule (approved)

After Stripe applied and the local finalise then fails for good:
- **no money moved** (`cancel_at_period_end` toggle; seat decrease; plan downgrade with
  `proration_behavior="none"`) -> **auto-revert** at Stripe -> `COMPENSATED`; if the revert
  itself fails -> `ESCALATED`;
- **money moved or an object was destroyed** (P0 delete; a PAID seat increase or plan
  upgrade) -> **never auto-revert** -> `ESCALATED`, a human rolls **forward**. Reverting a paid
  change would need a refund too — two more irreversible money movements to compensate for
  one. **Failure direction chosen deliberately: under-served-pending-human (recoverable,
  visible) over over-charged-silently (neither).**

The payment-**failure** paths F1-F5 are the other branch: Stripe applied the change but the
payment did not go through, so nothing was collected -> revert **and void the open invoice**
via the in-tree `_revert_to_previous_price` (and a quantity equivalent for `update_seats`),
matching the individual path exactly -> `FAILED`.

### 9e. Bounded retry of the local finalise (approved)

- **Retries only on transient classes: `OperationalError`, `InterfaceError`.** Anything else —
  `IntegrityError`, a re-validation `ValueError` — escalates at once. Retrying a logic failure
  only delays the page.
- **Bounded:** a fixed small number of attempts with short backoff; never an unbounded spin.
- **NAMED GATE-5 ASSERTION (binding, d4's wording):** *after the 60 s idle-in-transaction
  kill, the retry discards the dead connection, opens a fresh transaction outside the failed
  atomic block, and the injection proves the retry SUCCEEDS on the fresh connection — not
  merely that a retry happened.* Implementation: `connection.close()` before each retry so
  Django reconnects on next use; each attempt is its own top-level `atomic(durable=True)`,
  never nested in the one that failed.
- **Requires the local finalise to be idempotent** — each attempt re-reads, re-validates and
  writes absolute values (the new plan / quantity / flags), never deltas. A test proves it.

### 9f. Idempotency keys (condition 1)

Every Stripe mutation carries `idempotency_key=f"h28-licence-{intent.id}-{step}"`, with a
**distinct `step` per distinct call** (`apply`, `revert`, `void`). Stripe rejects a reused key
with different parameters, so apply and revert can never share one. Precedent:
`interval-change-refund-{pi_id}` in `_void_or_refund_side_effect_invoice`.

### 9g. The layered safety net — d4's words

(i) **bounded auto-retry = seconds**; (ii) **alert with an owner = minutes to hours**;
(iii) **the reconciliation detector = the backstop** that catches any `STRIPE_APPLIED` row
that fell through both.

**Alert channels (in-tree precedent, not invented):** an ERROR log, which becomes a Sentry
event where Sentry is initialised (`AutoGrader/settings.py:139-145`); and a best-effort email
to every active super admin, following `_notify_super_admins_offline_overage_pending`
(`license_service.py:3030`) — money-related, so not gated on notification preferences, and
never raises.

### 9h. Open items — NOT decided by this design

1. **Alerting depends on `SENTRY_DSN` being set in production; unverified from this
   environment.** If it is not, the super-admin email is the only live channel.
2. **Who owns money-reconciliation alerts, and what is the response time?** Organisational;
   with the user via d4. The code guarantees a human is **told**, not that one **acts**.
3. **Proper 3DS support for licence upgrades** (async confirmation instead of revert) —
   pre-existing gap on both paths; d4 is noting it for the user as a possible feature.
4. **H-32** (resume-and-finish sweeper for stuck intents) — registered, **not** in Change 1;
   priority contingent on the user's alert-ownership answer.

### 9i. The hard-kill case — correction accepted, two additions APPROVED INTO Change 1

**The premise corrected:** moving the Stripe call out of the transaction removes the 60 s
Postgres kill; the next wall is gunicorn's `--timeout 100` (`Dockerfile:84`, mirrored by
`WEBHOOK_REQUEST_HARD_TIMEOUT_SECONDS = 100`, `webhooks.py:74`). stripe-python 14.4.1 (as
installed) defaults to an 80 s timeout and `max_network_retries = 2`, ~240 s worst case per
call; `billing/imports.py` overrides neither. These functions make up to four calls in
sequence. **A killed worker runs no code, so no alert fires.** Change 1 turns a silent
*unrecorded* divergence into a *recorded but unalerted* one. d4: the doctrine does not accept
a fix that introduces a new silent-failure state, so both of the following are **required**.

**(1) Stale-intent check — APPROVED, required for Change 1 to land.** A periodic Celery task
on the existing infrastructure: list intents in `PENDING` / `STRIPE_APPLIED` older than
**~10 minutes** and alert through 9g's channels. **One DB query, zero Stripe calls.** It
restores "a human is TOLD" for the hard-kill case; whether a human ACTS remains the user's
question.

**(2) Per-REQUEST Stripe budget — APPROVED.**
- **Per request, not per call** (95): four calls at 15 s with one retry each is already
  4 x 30 = 120 s. A shared request deadline is passed down; each call's timeout is what
  remains of it.
- **Target the Stripe work at ~70-80 s, well under 100 s** (d4): a budget that uses the full
  100 s leaves no time for the local finalise, the compensation and the alert to run —
  recreating the problem it exists to solve.
- **NAMED GATE-5 ASSERTION:** make Stripe slow on **every** call in the sequence and assert
  the handler reaches its own error branch **and completes its alert** before 100 s. A single
  slow call cannot catch the multiplication.

### 9j. NEW — which layer supplies the idempotency key (verified in stripe-python 14.4.1)

95 asked whether stripe-python's own retries of a mutation carry an idempotency key. Read
from the installed library:

- **POST (`Subscription.modify`, `Refund.create`, `Invoice.void_invoice`, `Price.create`):**
  `_api_requestor.py:548-550` sets a **random** `Idempotency-Key` on every POST, once per
  logical call; `_http_client.py` builds `headers` once, **before** the retry loop, so every
  internal retry reuses it. **stripe-python's own retries are therefore already safe.**
- **But each logical call gets a FRESH random key**, so any re-attempt by **our** code — or a
  user re-submitting — is not deduplicated. Our intent-derived key (9f) is what covers that
  layer. Both layers are needed; they protect against different repeats.
- **V1 DELETE — P0's `Subscription.delete` — gets NO automatic key**: the same line applies it
  only to V2 deletes. **To verify in Stripe test mode (Gate 7), not asserted here:** my
  understanding is that Stripe ignores idempotency keys on DELETE entirely. If so, a retry of
  a delete whose first attempt succeeded but whose response was lost may come back as an
  **error** — making a successful, irreversible delete look like a failure.

**Consequence — the unknown-outcome rule (NEW, applies to every site):** a Stripe exception
does **not** always mean the mutation failed.
- **Definitive** (the request was processed and refused — e.g. `InvalidRequestError`): Stripe
  did not apply it -> `FAILED`. (`CardError` on an invoiced modify is **not** definitive — the
  in-tree individual path treats the swap as possibly live — so it takes the F1-F5 branch.)
- **Indeterminate** (`APIConnectionError` including timeouts, and 5xx `APIError`): **outcome
  unknown**. The intent stays `PENDING`; the code **reads Stripe** (`retrieve`) and compares
  against the target state — reached -> proceed as `STRIPE_APPLIED`; not reached -> `FAILED`;
  the read itself fails -> left `PENDING` for the stale-intent check to alert on.

**This is also a P1b trigger in today's code, not only a design detail:** every one of these
functions currently turns any `StripeError` — including a timeout whose request actually
landed — into `ValueError` and leaves local state unchanged. A timed-out-but-applied mutation
diverges today, deterministically, with no Postgres kill involved.

### 9k. Keeping a large change reviewable — commit sequence

Change 1 is large, and d4 reads line by line. Each commit carries the tests for its own
piece, so every commit is green on its own:

| # | Commit | Tests landing with it |
|---|---|---|
| 1 | **Reproduce-first tests only** — must FAIL on `b744c9f` (H2.1); output recorded | the reproductions themselves |
| 2 | Intent model + migration + admin registration; no behaviour change | model constraints, per-licence guard |
| 3 | Phase plumbing (A-D, `durable=True`, keys, unknown-outcome rule) on `cancel_license_subscription` — the simplest, compensable flow | its reproductions go green; 4 injection points |
| 4 | `update_seats` + F4/F5 + quantity revert helper | its reproductions; `requires_action` on seats |
| 5 | `change_license_plan`/`change_license_price` + F1/F2/F3 + F1's 4-point record | its reproductions; licence-path `requires_action` |
| 6 | P0 `convert_license_to_offline` | its reproductions; the unknown-outcome delete case |
| 7 | Bounded retry of the local finalise | **named assertion: succeeds on a fresh connection** |
| 8 | Alerting + stale-intent periodic check | alert fires; stale rows found; best-effort never raises |
| 9 | Per-request Stripe budget | **named assertion: slow on every call, alert completes < 100 s** |
| 10 | Detector-spec amendment (docs) | — |

**Gated as one change** (constraint (d)); the sequence is for reading, not for landing
piecemeal.

## 7. What is NOT claimed here

No code written. No test run. No gate run. The seven sites are **confirmed by code reading
against `b744c9f`, not reproduced**. No remedy has been demonstrated to work. Per H11.3
nothing here is "fixed", "secure" or "done".
