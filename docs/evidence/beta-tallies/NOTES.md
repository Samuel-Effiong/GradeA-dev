# R2 beta tallies follow the wallet charges (beta bug fix) - notes

Brief R2 from the SM (2026-11-12), owner-approved plan
team/PLAN_beta_tallies_from_wallet_2026-11-12.md. Base origin/beta
d9bdbdf58, rebased onto S10 (beta cf2424286), branch task/beta-tallies.
Runs under flock
~/.machine-fullsuite.lock, venv ~/Documents/Virtualenvs/beta_tallies_env.

## What changed

- billing/charges.py `charged_q(user=... | user_ids=...)`: the one charges
  rule (is_refunded false; Q(user_id=u) | Q(user_id null, wallet_id in u's
  wallets), no wallet join). The users list's credits_used_raw (S10) now
  imports it, with the same SQL shape; the tallies use it too.
- billing/beta_tallies.py: `charged_logs()` (CreditUsageLog with
  is_refunded false, owner Coalesce(user_id, the wallet's user) through a
  subquery, so a log whose wallet was deleted still counts by its own
  user_id), `charges_by_user()` (one grouped query per batch) and
  `reconcile(profiles)`. reconcile sets total_credits_used and the
  per-feature counters to the charges (raw unit), sets or clears the flags
  with AnalyticsService.usage_flags, rescores with
  AnalyticsService.conversion_score, and returns one Correction per changed
  profile. A profile that already matches is not written. Batches of 500:
  one unlocked read finds the drifted rows, then a short transaction locks
  them, re-reads their charges and bulk-updates them.
- billing/services.py: `usage_flags(total, allocation)` is the one flag rule
  (record_consumption, record_refund, reconcile); `conversion_score` is the
  scorer without the save (calculate_conversion_probability calls it).
  refund_credits passes record_refund the whole logged amount of every log
  it marks is_refunded (not the bucket-clamped amount); record_refund keeps
  the clamp at 0, then recomputes the flags and the score at once.
- billing/migrations/0075_r2_beta_tallies_from_wallet.py: reconcile over
  every profile, with the historical models. Reverse is a no-op.
- billing/tasks.py `reconcile_beta_tallies`, Beat "reconcile-beta-tallies"
  at 00:10 (before the 00:30 scoring), @single_instance(DAILY). One INFO
  line per corrected profile: user id, old -> new total, gap, fields.
- users/filters.py FILTER_HELP["at_80_percent"]: "Once marked, it stays
  marked." replaced by the approved sentence. No other copy in code (two
  old evidence files under docs/evidence quote it; left as records).

## Rulings (SM, 2026-11-12)

- First ruling (clear_flags switch: migration clears, nightly keeps) was
  replaced by the owner: the at-80% and cap flags ALWAYS match real usage.
  Migration, nightly task and record_refund all set and clear them;
  record_consumption uses the same rule. Allocation 0 leaves them alone.
- The sum counts every non-refunded log of the user, including charges
  from before joined_beta_at and from while the user was not
  beta-eligible (record_consumption skipped those).
- Matching rule Coalesce(user_id, wallet user) + is_refunded false is the
  one definition, written without a wallet join (S10 found the join drops
  logs whose wallet is gone). Folded into billing/charges.py after the
  rebase; a test pins reconciled total_credits_used == the users list's
  credits_used_raw for the same teacher (deleted-wallet, legacy and
  refunded logs included).
- Audit rows: docs/MIGRATIONS.md requires none for a data migration; these
  are analytics counters, so 0075 writes none.

## For the staging merge

- Staging already has billing 0075-0086 (0075_creditwallet_reserved_
  credits_check ... 0086_b1c_credit_requests): the merge needs a billing
  merge migration joining 0075_r2_beta_tallies_from_wallet and 0086.
- billing/holds.py (staging): settle() consumes `chargeable` through
  consume_credits (usage logs of exactly that amount) and Run.finish passes
  the same `chargeable` to record_consumption via _record_analytics, so the
  tally and the charges stay equal. Check at merge: (1) _record_analytics
  still passes `outcome.chargeable` (not `actual`); (2) unbilled credits
  (actual - chargeable) stay out of both; (3) holds refunds still go through
  SubscriptionService.refund_credits (billing/refunds.py), so they take the
  whole-log rule; (4) record_consumption on staging gets the same
  usage_flags change (services.py merge); (5) run test_r2_beta_tallies with
  holds on and off.

## Runs

- R2 red on b952c1a19 (tests only, base code; 9a31644d4 after the
  rebase): Ran 22, failures=4,
  errors=16.
- R2 green: Ran 22, OK.
- After the rebase onto S10 and the charged_q fold: R2 + S10 suites
  (test_r2_beta_tallies, users.tests_usage_filters,
  test_intent_signals_usage_filters, test_custom_ai_prompt_leads) Ran 75,
  OK.
- Plants (R2 suite): P1 reconcile counts refunded logs -> failures=1;
  P2 refund removes only the clamped amount -> failures=1; P3 nightly writes
  matching profiles -> failures=2. All restored.
- N1 near on 255d2ef52 (billing users students
  AutoGrader.tests_migration_rollback_defaults + the 59 standing guard
  labels present on beta, --parallel 4, fresh test DBs): Ran 5331,
  failures=1 (audit.tests_history_guard: reconcile's bulk_update field list
  was held in a name). Fixed in 51cae288d (fields spelled out, none tracked;
  a test keeps them equal to WRITTEN_FIELDS); R2 + history guard Ran 37 OK.
- N2 final near on 51cae288d (same labels, fresh test DBs): Ran 5332, OK
  (skipped=8).
