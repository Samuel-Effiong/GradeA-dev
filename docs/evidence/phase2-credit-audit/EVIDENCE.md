# Credit-transaction instrumentation evidence (§6 + §0.6)

Worktree: `Grade-Automator-Plus-epic-a-credit-audit`, branch
`task/epic-a-credit-audit`, started off `integration/epic-a` `d4a98da`,
fast-forwarded onto `integration/epic-a` `98c96bc` (beta's gate-runner
speedup work, `de6d191..d281b7f`) mid-task. This evidence is at the commit
that follows it.

## 0. Scope correction (§0.6)

The plan's §6 named `CreditLedger.record()` / `CreditUsageLog.record()` as
the chokepoint for all credit transactions. That's true for the ~18
`billing/services.py` call sites, but not universally: `CreditWallet.
consume_credits()` (the sole writer of `CreditLedgerType.CONSUME`) and the
batch path in `SubscriptionService.refund_credits()` both write via
`.build()` + `bulk_create()`, which never fires Django's `post_save`. A
hook placed only inside `record()` would have silently produced zero
`CREDIT_TRANSACTION` events for the highest-volume ledger type. Found by
grepping every `ledger_type=` at every call site rather than trusting the
plan doc's premise; verified independently by the Senior Manager; recorded
as `04_epic_a_implementation_plan.md` §0.6.

Resolution: instrument both paths.

- `CreditLedger.record()` — the `.save()` path, ~18 call sites.
- `AppendOnlyModel.after_bulk_create()` (new hook, `billing/immutable.py`,
  called from `AppendOnlyQuerySet.bulk_create()`) — the two paths that
  bypass `record()` entirely.

`CreditUsageLog` deliberately does not override the hook: its own
`bulk_create()` calls (paired 1:1 with the `CreditLedger` writes above)
stay silent, since the usage-log row is the same economic event as its
ledger row, not a second transaction.

## 1. Test suite

`billing/tests/test_credit_transaction_audit.py` — 5 tests: `record()`
path (grant), single-bucket consume, a two-bucket consume producing two
`CreditLedger` rows in one `bulk_create()` call (one event per row, not
per call), the batch-refund path, and a zero-amount refund (clamped to 0,
no ledger row built, no event). Each asserts FR-A-01's "exactly one
well-formed event" literally (`.count() == 1`, not `>= 1`), plus outcome,
actor, target_type/target_id, and metadata.

`python manage.py test --settings=settings_worktree billing.tests.test_credit_transaction_audit --noinput -v 2`

- Found 5 test(s)
- **OK**

## 2. Mutation testing

10 mutants (`mutate.py`) against `billing/models.py` (`record()`,
`after_bulk_create()`, `_emit_credit_transaction()`) and
`billing/immutable.py` (the `after_bulk_create()` wiring itself), one
protection weakened per mutant. Applied one at a time from collision-safe
copies, restore verified by md5 against the pre-mutation file after every
mutant (never `git checkout`); working tree confirmed to hold only the
intended instrumentation diff afterward (`git status`/`git diff`).

**Result: 10 / 10 KILLED**, clean on the first pass.

| id | protection weakened | expected test |
|----|---|---|
| R1 | `record()` never emits | `test_record_emits_exactly_one_event` |
| B1 | `after_bulk_create()` body emptied | `test_consume_emits_exactly_one_event` |
| B2 | bulk-path actor resolution always empty | `test_consume_emits_exactly_one_event` |
| W1 | `bulk_create()` never calls the hook at all | `test_consume_emits_exactly_one_event` |
| E1 | outcome hardcoded to FAILURE | `test_record_emits_exactly_one_event` |
| E2 | target_id uses actor id, not the ledger row's own id | `test_record_emits_exactly_one_event` |
| E3 | metadata credits field dropped | `test_record_emits_exactly_one_event` |
| E4 | metadata ledger_type hardcoded to GRANT | `test_consume_emits_exactly_one_event` |
| E5 | metadata credits value uses row.id, not row.amount | `test_record_emits_exactly_one_event` |
| D1 | multi-row bulk_create only emits for the first row | `test_consume_spanning_two_buckets_emits_one_event_per_ledger_row` |

Full per-mutant log: `mutation_log.jsonl.txt`. Script: `mutate.py.txt`
(paths inside it are absolute to this worktree, as run).

## 3. Regression — full suite

Run via `scripts/isolated-test-env.sh` (private Postgres 16 + Redis),
after fast-forwarding onto `integration/epic-a` `98c96bc` to pick up
gate-runner's `--parallel 4` speedup work (confirmed no path overlap
between that merge and this branch's touched files before merging).

`python manage.py test --settings=settings_worktree --parallel 4 --noinput -v 1`

- Ran 4689 tests in 285.881s (~4.8 min)
- **OK (skipped=28)**
- 0 `FAIL`/`ERROR` lines anywhere in the log (grepped, not just the final summary line)

Matches the Senior Manager's independently-verified baseline for
`integration/epic-a` at `98c96bc` (4684 tests, 305s) closely; the small
count/time delta is this branch's own 5 additional tests.

Note for the record: an earlier regression attempt (pre-merge, against
`integration/epic-a` `d4a98da`, before the speedup work landed) was
correctly stopped mid-run by the Senior Manager's instruction rather than
let finish, since it would have been superseded — not a failure, just
superseded work. That attempt also repeated a known anti-pattern from the
T2 evidence doc (a manual `nohup ... &` inside a tool-call shell instead
of the harness's own background-task mechanism), which is why it had to
be polled and killed by PID rather than tracked normally; the final
(kept) regression run above used the harness's background tracking
directly and completed cleanly.

## 4. Conclusion

Both credit-transaction write paths (`CreditLedger.record()` and the
`bulk_create()`-only consume/batch-refund paths) emit exactly one
well-formed `CREDIT_TRANSACTION` event per `CreditLedger` row, matching
plan §6/§0.6 and the outcome/metadata vocabulary from
`04_epic_a_implementation_plan.md`. No regressions anywhere in the
codebase from this change.
