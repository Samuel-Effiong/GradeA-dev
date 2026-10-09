# Audit retention sweep evidence (§7)

Worktree: `Grade-Automator-Plus-epic-a-retention-sweep`, branch
`task/epic-a-retention-sweep`, started off `integration/epic-a` `626cf08`
(already carries the credit-audit + grading-audit + PII-cleanup work).

## 0. Implementation note: §7.1's own later correction (§0.3b) overrides its code sample

The plan's §7.1 code sample wraps *both* sweeps in `with
allow_unsafe_mutation():`. The same document's §0.3b (a build-time
correction recorded during the T1 foundation work) supersedes that: because
`AuditEvent.mutable_fields = {"source_ip", "user_agent"}`, the PII
short-retention sweep can call plain `.update(source_ip=None,
user_agent=None)` and needs no escape hatch at all — only the row-delete
sweep (`sweep_audit_retention`) still needs `allow_unsafe_mutation()`,
since deleting a row has no `mutable_fields`-style exemption.

Implemented per §0.3b, not per the (superseded) §7.1 code sample:
`sweep_audit_retention` is the sole new caller of
`allow_unsafe_mutation()`; `sweep_audit_pii_short_retention` uses a plain
`.update()`. `billing/immutable.py`'s docstring updated from "two callers"
to "three callers" accordingly (§7.3).

## 1. What was built

- `audit/tasks.py` (new): `sweep_audit_retention` (deletes `AuditEvent`
  rows past their `retention_class` cutoff: 365 days GENERAL, 365*3 days
  STUDENT_RECORD) and `sweep_audit_pii_short_retention` (nulls
  `source_ip`/`user_agent` after `PII_SHORT_RETENTION_DAYS` = 90,
  independent of the row's own `retention_class` — X-4).
- `AutoGrader/settings.py`: `sweep-audit-retention-daily` (06:00) and
  `sweep-audit-pii-short-retention-daily` (06:30) added to
  `CELERY_BEAT_SCHEDULE` (after the existing 05:00 credit-bucket sweep)
  and to `BEAT_HEALTH_EXPECTATIONS` (daily interval, 2-day alert
  threshold, matching every other daily sweep's convention).
- `billing/immutable.py`: `allow_unsafe_mutation()` docstring updated to
  name its third caller.

## 2. Test suite

`audit/tests_retention_sweep.py` — 10 tests, matching §7.4 exactly:

- Cutoff-day fixtures per retention class (`RowDeleteCutoffTests`): a row
  one day past its class's cutoff is deleted, a row one day inside it
  survives — for both GENERAL and STUDENT_RECORD — plus a test that a
  GENERAL row past its own (shorter) cutoff is never left behind by the
  STUDENT_RECORD filter accidentally covering it (guards against the two
  filters being swapped).
- Run-twice idempotency, for both tasks: the second run's return value
  literally reports zero.
- PII short-retention cutoff (`PiiShortRetentionCutoffTests`): IP/UA
  nulled past 90 days, untouched within it; a STUDENT_RECORD row (3yr
  class) still gets its IP/UA nulled at 90 days but the row itself is not
  deleted (X-4's "independent clock" requirement, made concrete); a row
  with no IP/UA to begin with is excluded from the update count.
- Real concurrency (`ConcurrentSweepTests`, `TransactionTestCase`): two
  threads call the same sweep function at the same time against the same
  overlapping rows, barrier-synchronised (same pattern as
  `billing/tests/test_free_plan_activation_security.py`'s
  `ConcurrentActivationTests`). Asserts no exception escapes either
  thread, every eligible row is processed, and the two calls' reported
  counts sum to exactly the number of eligible rows (no double-processing,
  no under-processing) — for both the delete sweep and the null-out sweep.

`python manage.py test audit.tests_retention_sweep --settings=settings_worktree --noinput -v 2`

- Found 10 test(s)
- **OK**

## 3. Mutation testing

9 mutants (`mutate.py`) against `audit/tasks.py`, one retention protection
weakened per mutant: general/student cutoff arithmetic (2), the two
retention_class filters swapped, `allow_unsafe_mutation()` removed from
the delete sweep, the summary string mislabelling general/student_record,
the PII cutoff widened 10x, the PII sweep dropping `user_agent` from the
update, the PII sweep's `exclude()` guard removed, and the PII summary
hardcoded to 0. Applied one at a time from a collision-safe backup,
restore verified by md5 against the pre-mutation file after every mutant
(never `git checkout`); working tree confirmed to hold only the intended
diff afterward (`git status`/`git diff`, `md5sum audit/tasks.py` matching
the pre-mutation value).

**Result: 9 / 9 KILLED**, clean on the first pass.

Full per-mutant log: `mutation_log.jsonl.txt`. Script: `mutate.py.txt`
(paths inside it are absolute to this worktree, as run).

## 4. Regression — full suite

Run via `scripts/isolated-test-env.sh` (private Postgres 16 + Redis).

`python manage.py test --settings=settings_worktree --parallel 4 --noinput`

- Ran 4699 tests in 366.164s (~6.1 min)
- **OK (skipped=28)**
- 0 `FAIL`/`ERROR` lines anywhere in the log (grepped, not just the final
  summary line)

Also ran `audit`, `billing`, and `AutoGrader.tests_beat_health` together
first (1676 tests, OK) before the full suite, to catch anything close to
the touched files quickly.

Tail of the full run: `full_regression_summary.txt`.

## 5. Conclusion

Both retention sweeps behave exactly as §7 specifies: rows are deleted
strictly by their own retention class's cutoff (no cross-application
between GENERAL and STUDENT_RECORD), PII is nulled on its own independent
90-day clock regardless of the row's retention class, both are idempotent
under Beat's at-least-once redelivery, and both are safe under real
concurrent execution. No regressions anywhere else in the codebase from
this change.
