# Epic A: Sentry alert rules (a proposal for the founder)

**Status:** a proposal. Nothing here is in config; you create each rule by hand in the Sentry UI. Written for Epic A S8 (plan 08 §8.2), by ed (Security).

**Why these rules:** the audit trail records what happened. These alerts tell a person when something is going wrong while it is still happening. The app already sends every signal below: the metrics from `audit/metrics.py`, and the error logs through Sentry's logging integration. Only the rules are missing.

**Before you start:**
- Each rule lists the environments it belongs in (staging / beta / main):
  - "main" is production;
  - staging copies exist for the acceptance test and for the P0 plumbing rules;
  - rules 4 and 7 compare against normal traffic, so they are main-only (staging and beta traffic is too thin and too scripted to have a meaningful baseline).
- Set up each rule once per listed environment, choosing that environment in the rule.
- Owner: you, for now. Hand ownership over as the team grows.
- Choose who is notified. The proposal: P0 goes to your phone (email + push); P1 goes to email within the hour.
- The metrics are Sentry **metrics** (Explore → Metrics). They exist only once the code emitting them is deployed and has run at least once.

## The rules

| # | What it catches | Sentry signal | Condition | Priority | Environment | Owner (for now) |
|---|---|---|---|---|---|---|
| 1 | **Grading is failing** for users | metric `grading_failure_rate` (a 0/1 value per grading) | `avg()` > **0.05** over **15 min**, with at least 20 gradings in the window | P1 | main + beta | the founder |
| 2 | **The main AI model is down**, and grading is falling back to a backup | metric `model_fallback_rate` | `avg()` > **0.10** over **15 min** | P1 | main + beta | the founder |
| 3 | **Credits are being recorded wrongly**, e.g. a failed EXPIRE or a ledger that doesn't add up | metric `credit_ledger_anomaly` (counter; tag `kind`) | `sum()` ≥ **1** in **5 min** (any occurrence) | **P0** | main + beta + staging | the founder |
| 4 | **A new kind of error is spiking**: the earliest sign of a regression users aren't reporting | metric `reason_code_rate` (counter; tag `code`) | per `code`: the 1 h `sum()` > **3×** its own 7-day hourly average (Sentry's "percent change" alert, +200%) | P1 | main | the founder |
| 5 | **The audit trail itself is failing to write** | metric `audit_emit_failures_total` | `sum()` > **0** sustained for **5 min** | **P0** | main + beta + staging | the founder |
| 6 | **A burst of server crashes on open routes** (sign-in, register and other anonymous writes) | metric `reason_code_rate` with `code:SERVER_ERROR` | `sum()` ≥ **10** in **5 min** | P1 | main + beta | the founder |
| 7 | **Someone is hammering sign-in**, and the audit cap is holding events back | metric `audit_failed_auth_suppressed_total` (counter; tag `cap` = target or global) | `sum()` ≥ **50** in **15 min**. Also, any `cap:global` at all, since that means many accounts at once. | P1 | main | the founder |
| 8 | **A licence Stripe change was abandoned mid-way** (H-28), so Stripe and our records may disagree | issue alert on the ERROR log `Stale licence Stripe intents escalated:` (logger `billing.tasks`) | **any** new event | **P0** (once H-28 is on main) | main + beta (once H-28 ships) | the founder |
| 9 | **An event lost its details** because a call site sent fields the audit allow-list refuses | issue alert on the ERROR log `audit event metadata dropped` (`audit_kind=metadata_dropped`) | ≥ **1** in **1 h** | P2 (a bug to fix, not an outage) | main + beta + staging | the founder |

Rules 1–5 are the thresholds already written into `audit/metrics.py` (FR-A-10 / BE-A-09). Rules 6–9 are new in S8:
- Rule 6 separates a crash burst on open routes from rule 4's general spike. A crash on a sign-in door is urgent even if the 7-day average is also noisy.
- Rule 7 turns S1b's cap into a signal. The cap stops a sign-in attack from flooding the trail, but it should not hide that the attack is happening. The `FAILED_AUTH_CAPPED` summary rows in the trail give the detail afterwards.
- Rule 8 is d5's H-28 escalation: a Beat task that runs every 5 minutes and logs at ERROR when it finds an abandoned intent.
- Rule 9 is a code-quality signal.

## Setting up one rule, step by step (rule 3 as the example)
1. Sentry → **Alerts** → **Create Alert**.
2. Choose **Metric alert**, then **Custom metric**.
3. Metric: `credit_ledger_anomaly`; aggregation: **sum**; time window: **5 minutes**.
4. Environment: **production** (then repeat for each other environment the rule lists).
5. Trigger: **Critical** when sum **is above 0**. Leave the resolve threshold at its default.
6. Action: notify **you** by email and push (P0).
7. Name it: `P0 credit ledger anomaly`. Save.

For rules 8 and 9 choose **Issue alert** instead:
- condition: "A new issue is created" OR "the issue is seen more than N times in T";
- filter: *message contains* the quoted text;
- environment: production.

## Acceptance (FR-A-10, plan 08 §8.2)
With the rules set up in **staging**, a scripted reason-code spike must fire rule 4 (or rule 6), and **you confirm the notification arrived**. 1a records it. A spike script will be provided for staging only, when you are ready.

## What these alerts do not do
- They never include personal data. Metric tags are codes and kinds only, and the logs named above carry ids and counts, never emails.
- They do not page for single user errors: a wrong password, an expired code or a refused file. Those are the reason codes rule 4 watches in aggregate.
