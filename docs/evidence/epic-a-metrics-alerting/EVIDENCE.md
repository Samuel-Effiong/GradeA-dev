# Evidence — Epic A §9 / BE-A-09 alertable metrics

Worktree: `Grade-Automator-Plus-epic-a-metrics-alerting`, branch
`task/epic-a-metrics-alerting`, off `integration/epic-a` `97813ca`
(includes the grading-audit and CRUD-audit branches already landed there).
This evidence at commit (filled in after commit, see §6).

## 1. Background

BE-A-09: "Emit alertable metrics for grading failure rate, model fallback
rate, credit ledger anomalies, and the rate of each reason code from
BE-A-06." Mechanism agreed with the user: Sentry's own metrics API
(`sentry_sdk.metrics`, confirmed present in the pinned `sentry-sdk==2.68.0`
— `count`/`gauge`/`distribution`), already wired into this app in prod
(`AutoGrader/settings.py`) — no new dependency, no new service. This
module and its call sites only **emit** the five named+tagged signals;
wiring the actual Sentry Alert/Monitor rules in the UI is a follow-up for
the user, using the threshold table documented in `audit/metrics.py`'s
own docstring so nobody has to re-derive it:

    grading_failure_rate        >5% over a rolling 15 min window
    model_fallback_rate         >10% over a rolling 15 min window
    credit_ledger_anomaly       any occurrence - page immediately (P0)
    reason_code_rate{code=...}  any single code >3x its own 7-day
                                 trailing average
    audit_emit_failures_total   >0 sustained for 5 min

## 2. `audit/metrics.py` — the Sentry wrapper

`count(name, value=1, tags=None)` and `distribution(name, value, tags=None)`.
Degrades to a safe no-op via `sentry_sdk.is_initialized()` (confirmed
present on this pinned version) whenever Sentry isn't live — local/test
envs, or a deploy that hasn't installed `sentry-sdk` yet (mirrors
`AutoGrader/settings.py`'s own guarded import, for the same reason). Every
call is wrapped in its own `try/except`: a metrics failure must never be
allowed to fail the grading run, the audit write, or anything else it only
describes.

`distribution` is used for the two *rate* signals (#1, #2), emitted as a
0.0/1.0 sample per triggering event rather than pre-aggregated — Sentry's
own `avg()` over a window on a distribution of 0/1 samples **is** the rate,
the standard technique for this API. `count` is used for the three
*occurrence* signals (#3, #4, #5).

## 3. Where each signal is emitted

**#1 `grading_failure_rate` and #4 `reason_code_rate` and #5
`audit_emit_failures_total`** — all three hook into `audit/emitter.py`'s
`emit()`, the single chokepoint every audited action already funnels
through (same "instrument the chokepoint, not every caller" principle the
rest of Epic A uses):

- `_emit_alertable_metrics()` runs once per event that is actually
  **stored** (after `AuditEvent.objects.create()` succeeds), from the
  validated `action`/`outcome`/`metadata`/`reason_code` — not the
  caller-supplied ones — so a rejected or malformed write can't feed a
  false metric.
- `#1`: for `GRADING_COMPLETED`/`GRADING_FAILED`, a distribution sample of
  `0.0` (success) or `1.0` (failure).
- `#4`: for any action carrying a `reason_code` (already in production use
  — `users/serializers.py`'s `ACCOUNT_LOCKED`, `users/views.py`'s
  `REFRESH_TOKEN_MISSING`/`REFRESH_TOKEN_INVALID`), a count tagged
  `{code: reason_code}`.
- `#5`: in `emit()`'s own store-failure `except` block — specifically the
  one FR-A-11 exists to swallow — a count tagged `{action: label}`. This
  is the one signal that fires on a *rejected*-from-storage write, not a
  successfully stored one, since that's exactly what it exists to detect.

**#2 `model_fallback_rate`** — also emitted from the same chokepoint, for
`GRADING_COMPLETED` only, but the signal it needs (which model actually
served the run) did **not** already reach `assignments/tasks.py:515`'s
`emit()` call, exactly as flagged before starting this work. Investigation:

- OpenRouter's own fallback routing (`extra_body={"models": sub_models}`
  in `ai_processor/services.py`'s `__ai_model`) means the model that
  *actually* answers a call can differ from `MAIN_MODEL`
  (`"x-ai/grok-4.3"`), routing instead to one of `GRADING_FALLBACK_MODELS`
  (`["deepseek/deepseek-v4-pro"]`) — grading/extraction never falls back
  further than that short list (never to a nano-tier model — see the
  comment at `execute_graded_task`).
- The model that actually served a call is already captured via
  `response.model` (`_response_model_name`) and threaded onto the graded
  result as `grading_model` — in **both** the single-pass path
  (`json_data["grading_model"] = model_name`, line ~3935) and the batched
  path (`_build_overall_grading_summary`'s `summary["grading_model"]`,
  line ~2908) — and from there onto `submission.feedback` via
  `_populate_and_save_grade` (`students/services.py`), which
  `grade_engine_async` receives back as its own `submission` variable.
- So `submission.feedback.get("grading_model")` at the `GRADING_COMPLETED`
  emit call site *is* the answer, without any new instrumentation inside
  `ai_processor/services.py` — it only needed to be read back and threaded
  into the existing `metadata={...}` dict. `"model"` was **already** in
  both `GRADING_COMPLETED`'s and `GRADING_FAILED`'s
  `audit/metadata.py METADATA_ALLOWLIST` entries, so no allow-list change
  was needed either.
- Scoped to `GRADING_COMPLETED` only, not `GRADING_FAILED`: a failed run
  never reaches the point where `grading_model` would have been captured
  (no persisted result exists to read it from), so there is nothing
  reliable to report — documented in code rather than guessed at.
- The chokepoint then compares that model against
  `ai_processor.services.GRADING_FALLBACK_MODELS` (local import, kept out
  of the chokepoint's module-load-time import surface — that module pulls
  in the OpenAI client).

**#3 `credit_ledger_anomaly`** — also confirmed, per the investigation
brief, to need its own detection rather than living at the emitter
chokepoint: `CREDIT_TRANSACTION` *is* an already-audited action
(`billing/models.py::_emit_credit_transaction`), but the anomaly detection
itself — a negative running balance, or an EXPIRE/REFUND that doesn't
reconcile — is not something the audit write already computes. Found and
reused three pieces of *existing* reconciliation logic, adding no new
math:

1. **Negative running balance** — `billing/models.py::_check_ledger_anomaly`,
   called from `_emit_credit_transaction` right after every ledger row is
   recorded (covers both the ~18 `record()` call sites and the
   `bulk_create`-based `after_bulk_create` paths, since both funnel
   through `_emit_credit_transaction`). Reuses
   `CreditWallet.total_remaining_credits()` — the same aggregate the rest
   of the app already reads a wallet's balance through. Best-effort, not a
   strict invariant: it reads the wallet *after* this row (and, for
   CONSUME, the paired bucket update) have been written, and only when the
   ledger row's `actor` resolves to the wallet's own user — documented in
   the function's own docstring as a deliberate false-negative trade-off
   for an *alert*, not enforcement.
2. **REFUND that doesn't reconcile** — `billing/services.py::refund_credits`
   already clamps `amount = max(0, min(log.amount, bucket.used_credits))`
   to avoid pushing `used_credits` negative; when the clamp actually
   reduces the amount (`amount != log.amount`), the usage log's claimed
   consumption no longer matches what the bucket can give back — exactly a
   non-reconciling REFUND. A second branch (a usage log whose bucket row
   is gone entirely) also emits the anomaly, though see §4 for why that
   branch has no test — it's already unreachable via the ORM under normal
   deletion, same as before this change.
3. **EXPIRE that doesn't reconcile** — `billing/tasks.py::cleanup_expired_credit_buckets`
   already has a per-bucket `try/except` around `SubscriptionService.expire_bucket()`
   that logs `"Failed to reconcile expired bucket..."` on failure — that
   log line **is** the existing detection of a non-reconciling EXPIRE; the
   anomaly metric was added into the same `except` block, not a new one.

## 4. Test suite

19 new tests in `audit/tests_metrics.py`, all mocking
`audit.metrics.count`/`.distribution` (real Sentry calls can't run in
tests) at the module that imported them — asserting each signal fires
**exactly once** per triggering event (`assert_called_once_with` / exact
`call_args_list`), not `>=1`, so both a missing call and an accidental
double-emit fail:

- `MetricsModuleNoOpTests` (7): no-op when not live, calls through when
  live, a Sentry-side exception never raises, `is_initialized()` itself
  raising is treated as not-live, `sentry_sdk` missing entirely is a
  no-op — for both `count` and `distribution`.
- `GradingFailureAndFallbackRateMetricTests` (5): a completed grading on
  the main model emits `grading_failure_rate=0.0` and
  `model_fallback_rate=0.0`; on the fallback model, `model_fallback_rate=1.0`;
  with no model captured, no fallback signal at all; a failed grading
  emits `grading_failure_rate=1.0` and **never** a fallback signal (even
  when a model happens to be in the metadata); a non-grading action emits
  neither.
- `ReasonCodeRateMetricTests` (2): a reason code emits the rate metric
  exactly once; no reason code emits nothing.
- `AuditEmitFailuresMetricTests` (3): a store failure emits the metric
  exactly once; a successful store never does; a *rejected* write (fails
  validation before ever reaching `AuditEvent.objects.create`) never does
  either — confirms #5 is scoped to the store-failure branch specifically,
  not "any emit() problem".
- `CreditLedgerNegativeBalanceAnomalyMetricTests` (2): a ledger write that
  leaves a wallet negative (via `used_credits > total_credits`, which
  bypasses the per-bucket `remaining_credits` property's clamp but not the
  raw aggregate `total_remaining_credits()` reads from — simulating the
  bug class this metric exists to catch, not inventing a new one) emits
  the anomaly; a healthy balance doesn't.
- `CreditLedgerRefundAnomalyMetricTests` (2): a clean refund emits
  nothing; the exact out-of-band-bucket-reset scenario
  `test_credit_refund.py::test_refund_clamps_instead_of_going_negative`
  already exercises emits `refund_mismatch` exactly once. (No third test
  for the `refund_no_bucket` branch — see the comment in
  `tests_metrics.py`: `CreditUsageLog.bucket` is NOT NULL, so
  `refund_credits()`'s own `select_related("bucket")` INNER JOINs it, and
  deleting the bucket makes the log invisible to that query entirely
  before the loop this metric lives in ever runs. That branch was already
  defensive/unreachable-by-the-ORM before this change — its own comment in
  `billing/services.py` says so — and the metric call is left in place as
  the same kind of defense-in-depth as the branch itself.)
- `CreditLedgerExpireAnomalyMetricTests` (2): a failed bucket expiration
  emits the anomaly exactly once; a clean one emits nothing.

Plus 2 tests extending the existing
`assignments/tests_grading_audit_events.py::GradeEngineAsyncOutcomeAuditEventTest`
(the only place that exercises the real `grade_engine_async` call site,
not a direct `emit()` call): a successful grade with
`submission.feedback = {"grading_model": "x-ai/grok-4.3"}` set threads it
into the stored event's `metadata["model"]`; a submission with no captured
model (`feedback = None`) doesn't raise and leaves `metadata["model"]`
unset, rather than an `AttributeError` on a bare `.get()`.

`python manage.py test audit.tests_metrics assignments.tests_grading_audit_events --settings=settings_worktree --noinput`

- Found 31 test(s)
- **OK**

Also ran the wider adjacent suite before the full regression:
`audit assignments.tests_grading_audit_events billing.tests.test_credit_transaction_audit billing.tests.test_credit_refund billing.tests.test_rollover_not_double_counted billing.tests.test_annual_mid_cycle_grants billing.tests.test_append_only_audit_tables`
— **228/228 OK**.

## 5. Mutation testing

13 mutants (`mutate.py`) against `audit/metrics.py`'s no-op/exception
guards, the three `audit/emitter.py` chokepoint hooks, the
`assignments/tasks.py` model-signal wiring, and all three
`credit_ledger_anomaly` call sites. Applied one at a time from
collision-safe copies, restore verified by md5 against the pre-mutation
original after every mutant (never `git checkout`), confirmed both
programmatically (`restored_md5_ok`) and by `git status --short` showing a
clean tree matching the intended changes afterward.

**Result: 13 / 13 KILLED**, clean on the first pass.

| id  | protection weakened | expected test |
|-----|---|---|
| M1  | `grading_failure_rate` success/failure value swapped | `failed_grading_emits_rate_one...` |
| M2  | `model_fallback_rate` scoping removed (fires on GRADING_FAILED too) | `failed_grading_emits_rate_one...` |
| M3  | `model_fallback_rate` in/out-of-list condition inverted | `completed_grading_on_the_fallback_model...` |
| M4  | `reason_code_rate` never emitted | `event_with_a_reason_code_emits...` |
| M5  | `audit_emit_failures_total` not emitted on store failure | `store_failure_emits_the_failure_metric...` |
| M6  | `metrics.count()` skips its not-live guard | `count_is_a_noop_when_sentry_is_not_live` |
| M7  | `metrics.distribution()` skips its not-live guard | `distribution_is_a_noop_when_sentry_is_not_live` |
| M8  | `metrics.count()` no longer swallows a Sentry-side exception | `a_sentry_call_failure_never_raises` |
| M9  | grading-completed model signal never read from `submission.feedback` | `successful_grade_emits_exactly_one_completed_event` |
| M10 | negative-balance anomaly condition inverted | `ledger_write_that_leaves_a_negative_balance...` |
| M11 | negative-balance check never called from `_emit_credit_transaction` | `ledger_write_that_leaves_a_negative_balance...` |
| M12 | `refund_mismatch` anomaly condition removed | `refund_that_cannot_fully_reconcile...` |
| M13 | `expire_failed` anomaly metric removed from the existing except block | `failed_bucket_expiration_emits_the_anomaly...` |

## 6. Regression — full suite

Run via `scripts/isolated-test-env.sh` (private Postgres 16 + Redis, CI's
fake credentials), on this worktree's `integration/epic-a` base (`97813ca`).

`python manage.py test --settings=settings_worktree -v 1 --noinput`
(every app, no labels, no `--keepdb`)

- Ran 4754 tests in 588.622s (~9.8 min)
- **OK (skipped=26)**

## 7. Conclusion

All five BE-A-09 signals are emitted, with their proposed alert thresholds
documented in `audit/metrics.py` for the user to wire into Sentry's own
Alert/Monitor UI. Three (#1, #4, #5) live at the `audit/emitter.py`
chokepoint alongside the rest of Epic A's audited actions; #2 threads an
already-existing-but-unpropagated signal (`grading_model`) into that same
chokepoint; #3 reuses three pieces of pre-existing reconciliation logic in
billing rather than deriving new balance math, per the explicit "don't
build new reconciliation math" guidance. The module degrades to a safe
no-op wherever Sentry isn't live and never raises into a caller. No
regressions anywhere in the codebase from this change.

Post-commit sha256 (from `git show <sha>:<path>`, not the working copy —
pre-commit hooks can rewrite a file after it's written):

```text
<filled in after commit — see next commit>
```
