# H-69: audit survey of the management commands that write data

- **Author:** ed (Security), 2026-09-30. This is a read-only survey. No code changed, no test ran, and no production data was read.
- **Surveyed:** beta `abeda10` and the bundle 4 tip `task/beta-batch-4` `e190f06`. Epic A (`phase2/epic-a` `ca35971`) was used for what the `audit` app already records.
- **Method:** each command on bundle 4 was read, along with the service or signal each one writes through, and matched against Epic A's `audit` app:
  - `audit/emitter.emit`;
  - the automatic history in `audit/history.REGISTRY`;
  - `CREDIT_TRANSACTION` on `CreditLedger` writes.

## The short version

- **Bundle 4 has 24 commands. 18 write data:**
  - 15 are operator one-offs or repairs;
  - 2 have HIGH findings that come before any audit work (`backfill.py`, `grading_benchmark`);
  - 1 is gated test tooling (`run_stripe_live_qa`).
- **Explicit audit events.** No command emits an audit event on any line. The one partial record is `remediate_student123_passwords`, which writes a JSONL file and a log line because `audit` isn't on beta.
- **What Epic A records automatically.** A command's writes are recorded only when they go through `.save()` / `.create()` on a registered model, and only for the registered fields:
  - `CustomUser`: user_type, is_active, is_staff, is_superuser, school_id;
  - `UserSubscription`;
  - `LicenseSubscription`;
  - `SchoolCreditAllocation`;
  - `StudentCourse`: enrollment_status and course_id only;
  - `StudentSubmission`.

  Outside a request, those events carry **actor SYSTEM**. So even where a command is recorded, the record doesn't say which super admin ran it. `.update()` and `bulk_update` record nothing unless the call site uses `record_bulk`. Most commands use one or the other.
- **The main recommendation (one piece of plumbing, not 18 emits).** Add an Epic A `command_actor(user, command=<name>)` context, alongside `current_request_actor`:
  - the history signals, `record_bulk` and `emit` pick it up;
  - every command's implicit events then carry the super admin named by `--by`, with `source="command"`;
  - explicit emits are needed only where no registered model is written.
- **One design question for the SM.** `ADMIN_ACTION`'s metadata allowlist is only `{"source"}`. The events need the command's name too. Either use `source="command:<name>"`, or add a `command` key to the allowlist (`audit/metadata.py`).

## Which line has which command

| Command | beta abeda10 | bundle 4 e190f06 | phase2/epic-a ca35971 | integration/epic-a 19e487e |
|---|---|---|---|---|
| `resolve_licence_stripe_intent` | – | **added** (H-28) | – | – |
| `backfill_receipt_urls` | old version (`.save()`) | **changed** (conditional `.update()`) | old version | old version |
| `audit_volume_report` (read-only) | – | – | **only here** | yes |
| `backfill_pending_student_invites`, `remediate_student123_passwords` | yes | yes | yes | **missing** |
| the other 20 | yes | yes | yes | yes |

Nothing that writes exists only on Epic A. Epic A's only extra command is `audit_volume_report`, which reads only.

## Proposed actor and source

- **An operator running a command:** actor = the SUPER_ADMIN named by `--by <email>`, metadata `source="command"`. It is resolved like `resolve_licence_stripe_intent._super_admin` already does it: the user must exist and be a SUPER_ADMIN, or the command refuses. `--by` is required together with `--apply` / `--execute` / `--fix`, and a dry run emits nothing.
- **Beat:** actor SYSTEM, `source="beat"`. None of the surveyed commands is scheduled. Their Beat equivalents (`sweep_missing_receipt_urls`, the nightly price sync) are tasks, not commands, and are outside this survey.

## HIGH: fix these before, or together with, the audit work

### H1. `billing/management/commands/backfill.py`: runs live Stripe writes when imported
- **What it writes:**
  - on Stripe, a subscription schedule per candidate, via `StripeSubscriptionScheduleService.schedule_plan_change_on_stripe`;
  - locally, `UserSubscription.stripe_schedule_id`, via `.save(update_fields=[...])`.
- **The problem:**
  - It has no `Command` class. It's a module-level script placed in `management/commands/`, so `manage.py backfill`, or any import of the module, runs the loop.
  - There's no dry run and no confirmation.
  - Present on beta, bundle 4 and Epic A.
- **Audit today:** none. `stripe_schedule_id` isn't a registered field, so there's no event even on Epic A.
- **Import check (SM request):** nothing on beta, bundle 4 or `phase2/epic-a` imports it. I searched for:
  - `commands.backfill`, `call_command("backfill")`;
  - the generic loaders (`load_command_class`, `get_commands`, `find_commands`, `import_module`, `__import__`, `runpy`).

  The generic importers load only named signals modules, `<app>.exceptions/errors/uploads`, or benchmark test modules. Django's test discovery only matches `test*.py`. Running `manage.py backfill` is the only way it triggers.
- **Proposed fix (SM agreed, not done here):** move it out of `management/commands/` (for example to `scripts/`, never imported), or wrap it in a `Command` that defaults to a dry run and needs `--apply --by`.
- **Audit after the fix:** one `SUBSCRIPTION_CHANGE` per row. Target the UserSubscription id, with `changed_fields=["stripe_schedule_id"]` (ids only), actor `--by`, `source="command"`. A failure is `FAILURE` / `PROVIDER`.
- **Priority:** HIGH (money: live Stripe schedules).

### H2. `ai_processor/management/commands/grading_benchmark.py` (live/record mode): mints an account and credits, with no production guard
- **What it writes:**
  - an active TEACHER `grading-benchmark@benchmark.local`, with a random password;
  - a `SubscriptionPlan` "Grading Benchmark Plan", `is_active=True`, with 5,000,000 monthly credits;
  - an active `UserSubscription` for a year;
  - a `CreditWallet`, plus a 5,000,000-credit `CreditBucket` whenever the balance is under 500k;
  - `BenchmarkRun` / `BenchmarkQuestionOutcome` rows (`history.record_run`).
- **`--teacher-email`** runs the live benchmark as a real teacher, which spends that teacher's credits on real model calls.
- **The problem:** nothing refuses a production database. The SM has told the founder not to run it on Railway.
- **Audit today:**
  - beta: none;
  - Epic A: the `UserSubscription` create gives a `SUBSCRIPTION_CHANGE` as SYSTEM;
  - the teacher account gets no event (by design, `_privileged_user`);
  - the credit bucket gets no `CREDIT_TRANSACTION`, because no `CreditLedger` row is written.
- **Proposed fix (SM wording):** refuse to run unless `settings.DEBUG`, or an explicit `--i-know-this-is-not-production` together with a non-production environment check. The guard is the fix. Once it's in place, the benchmark writes only to non-production databases and needs no audit.
- **Priority:** HIGH (credits and access).

## Commands that write, by priority (money, credits, access first)

| # | Command | Writes | Safety today | Audit today (beta / Epic A) | Proposed audit | Priority |
|---|---|---|---|---|---|---|
| 1 | `billing/…/replay_stripe_events.py` | With `--apply`, re-runs webhook handlers (credits, subscriptions, licences, and money: e.g. `stripe.Refund.create`), plus the `StripeEvent` status/attempts (`.update()`) | Dry run by default; `--apply` | none / the handlers' own writes show up as SYSTEM history and `CREDIT_TRANSACTION`, but nothing records **who replayed which event** | One `ADMIN_ACTION` per replayed event (target `stripe_event`, id = the event's pk, outcome from the handler), actor `--by`. Run each replay inside `trace_context()`, so the handler's events share its trace_id and can be joined | HIGH (money) |
| 2 | `billing/…/resolve_licence_stripe_intent.py` (bundle 4 only) | With `--apply`, the intent's status, `resolved_at`, `resolved_by`, `resolution_note` (`.update()`) | Dry run by default; `--apply --outcome --by --note`, and `--by` must be a SUPER_ADMIN | the row itself keeps `resolved_by` and the note / no event | **Already assigned:** ed's post-merge-down Epic A task (SM ruling). One `ADMIN_ACTION`, target the intent, before/after status, actor = `--by` | HIGH (money), planned |
| 3 | `users/…/remediate_student123_passwords.py` | With `--execute`, `password` becomes unusable and `token_epoch` + 1 (`.update()`, compare-and-set) | Dry run by default; `--execute` | a JSONL report (**holds emails**) + a `users.remediation` log line / none (password and token_epoch aren't registered) | One `ADMIN_ACTION` per reset account (target user id, ids only), actor `--by`. On Epic A the audit event replaces the JSONL, and with it the PII file | HIGH (access) |
| 4 | `classrooms/…/backfill_pending_student_invites.py` | Student `password` (set), `is_active`, `must_change_password`, activation token/expiry cleared; sends the login email | `--dry-run` exists but **isn't the default** | none / `is_active` gives `PERMISSION_CHANGE` as SYSTEM (`source="save"`); the password and token clearing aren't recorded | With `command_actor`: the existing `PERMISSION_CHANGE` carries `--by`. Plus one `ADMIN_ACTION` per student for the password and invite (ids only). Also make dry run the default and require `--apply --by` | HIGH (access) |
| 5 | `users/…/add_whitelist.py` | `BetaWhitelist` rows (`get_or_create`, `is_active=True`), which grant beta access | Writes straight away; no dry run | none / none (not registered) | One `PERMISSION_CHANGE` per new row (target the `beta_whitelist` row id, **no email**), actor `--by` | HIGH (access) |
| 6 | `billing/…/backfill_billing_transactions.py` | New `BillingTransaction` rows from Stripe events and `LicenseBillingRecord`s (money *records*, no money moves) | `--dry-run`, not the default | none / none | One `ADMIN_ACTION` per run (counts), plus the new rows' ids. Per-row events aren't needed: the rows *are* the record, and each is stamped with its source | MEDIUM (money records) |
| 7 | `billing/…/reconcile_overage_prices.py` | With `--fix`, `SubscriptionPlan.overage_block_price` (`.save()`), aligned to Stripe | Report-only by default; `--fix` | none / none (`SubscriptionPlan` isn't registered) | One `ADMIN_ACTION` per plan fixed, before/after cents, actor `--by` | MEDIUM (quoted price; what customers are charged doesn't change) |
| 8 | `billing/…/reconcile_stripe_prices.py` (`billing.price_reconciliation.reconcile_prices`) | A `PriceReconciliationRun` + one `PriceReconciliationResult` per price. It also **syncs local `SubscriptionPlan` amounts and product to Stripe** (`.update()`, `_apply_stripe_values`). The command's docstring says "Detection only … no billing state", but `_check_one` calls `_apply_stripe_values` for every syncable mismatch on an active price, so an operator's run changes local prices | Runs straight away; no dry run or `--fix` flag | the Run/Result rows record what changed, with before values / same | Run/Result already form a history. Add `triggered_by` (`--by`) to the Run, or one `ADMIN_ACTION` per run, so an operator run is told apart from the nightly one. Also fix the docstring | MEDIUM (prices) |
| 9 | `billing/…/seed_plan_features.py` | `PlanFeature` / `PlanFeatureInclusion` (`update_or_create`) and `SubscriptionPlan.carry_over_percent` (`.save()`), which moves how many credits roll over | `--dry-run`, not the default | none / none | One `ADMIN_ACTION` per changed plan or feature, before/after, actor `--by` | MEDIUM (credits via rollover) |
| 10 | `classrooms/…/recalculate_final_grades.py` | With `--apply`, `StudentCourse.final_grade` (`.save(update_fields=["final_grade"])` under a row lock); `--allow-clear` clears a grade that has been seen | Dry run by default; `--apply`; `--allow-clear` only with `--enrollment` | the printed output is the reversal record / none (`final_grade` isn't registered) | Register `final_grade` on `StudentCourse` (as a `GRADE_CHANGE`, `touches_student_record=True`), so every path, including the post-save signal, is recorded; `command_actor` supplies `--by` | MEDIUM (student records) |
| 11 | `billing/…/backfill_receipt_urls.py` | `BillingTransaction.receipt_url` (bundle 4: a conditional `.update()`, only when the field is null) | `--dry-run`, not the default | none / none | One `ADMIN_ACTION` per run (a count). A receipt link has no before value worth recording | LOW |
| 12–15 | `assignments/…/backfill_assignment_rigor.py`, `repair_question_blooms_levels.py`, `strip_duplicate_option_letters.py`, `strip_html_from_assignment_titles.py` | `Assignment` content: rigor fields, `questions`, `title` (`bulk_update`) | `--dry-run`, not the default | none / none (`Assignment` isn't registered, and `bulk_update` sends no signals) | One `ADMIN_ACTION` per run (a count, plus the assignment ids) with `source="command"`. Per-row `ASSIGNMENT_UPDATE` isn't needed: these are content repairs, not permission or money changes | LOW |
| 16 | `billing/…/run_stripe_live_qa.py` | Its own test users/subscriptions and `StripeEvent` rows, then deletes them; Stripe **test mode** only | Refuses unless `settings.ENABLE_STRIPE_LIVE_QA` is set and the key is an `sk_test_` key | n/a | None needed (test tooling, gated) | LOW |

H1 (`backfill.py`) and H2 (`grading_benchmark`) are above. Together with the 16 commands here (rows 12–15 are four commands), that makes 18 commands that write. The other 6 don't write to the database (below).

## Commands that don't write to the database

`answer_extraction_benchmark` and `extraction_benchmark` write report files. `grading_benchmark_history` merges local JSONL history files. The rest only read:
- `grading_eval`;
- `audit_email_track_separation`;
- `audit_school_admins`;
- `audit_volume_report` (Epic A).

None needs an audit event.

## Suggested order of work (for the SM)

1. **H1 and H2:** the guards. These are small, standalone, beta-line fixes.
2. **Epic A:** `command_actor` + the `--by` convention, and the SM's metadata ruling. After that, rows 3, 4, 10 (with registering `final_grade`) and 5.
3. **Rows 1 and 2:** replay and resolve-intent. Resolve-intent is already queued as ed's post-merge-down task.
4. **Rows 6–9, then 11–15:** one per-run `ADMIN_ACTION` helper covers most of them.

Across all rows: dry run should be the default everywhere a command writes. Today `backfill_pending_student_invites`, `add_whitelist`, `backfill_billing_transactions`, `seed_plan_features`, `backfill_receipt_urls` and the four assignment repairs write unless you pass `--dry-run`.
