# Append-only audit tables (CreditLedger, CreditUsageLog)

**Status:** enforced in application code (`billing/immutable.py`), applied by
migrations `billing/0059` and `billing/0060`. NOT enforced in the database —
see "What this does not cover".

## What changed and why

Both models' docstrings called themselves an immutable audit trail. Nothing
enforced it. Measured against production with Django's own deletion collector
(read-only), deleting a single teacher would have destroyed **17,761
`CreditLedger` rows** along with the account, because `CreditLedger.user` was a
`CASCADE` foreign key. `CreditUsageLog` died the same way one level deeper,
through `wallet`.

Three separate holes, all now closed:

| Hole | Was | Now |
| --- | --- | --- |
| Cascade from the account | `user` FK `CASCADE` | plain `user_id` column, no relation |
| Cascade from wallet/bucket | `CASCADE` | `DO_NOTHING`, `db_constraint=False` |
| Silent UPDATE | `bucket`/`course`/`school` `SET_NULL` | `DO_NOTHING`, `db_constraint=False` |
| HTTP write path | `CreditLedgerViewSet` was a `ModelViewSet` with POST/PATCH/DELETE | `ReadOnlyModelViewSet` |

Identity is now stored as **values** (`user_id`, `user_email`) captured at write
time, not as a foreign key. A relation ties the audit record's survival to
another mutable row, and an audit trail that disappears with its subject is not
an audit trail. Use `CreditLedger.record()` / `.build()` and
`CreditUsageLog.record()` / `.build()` so the email snapshot is never forgotten.

## What this does not cover

This is **application-level** enforcement. It is bypassed by:

- raw SQL (`cursor.execute`, `Model.objects.raw`, `RunSQL` in a migration)
- `QuerySet._raw_delete()`
- `TRUNCATE` — row-level signals and triggers both miss it, and
  `manage.py flush` uses it
- **anything connecting outside Django** — `manage.py dbshell`, psql, the
  Railway console, a future service

The application also connects as the `postgres` superuser, so a
`REVOKE UPDATE, DELETE` would be silently ignored (superusers bypass permission
checks). There is therefore no privilege barrier behind this layer.

Describe the result as *"protected against application-level mistakes"*, not as
a compliance guarantee. Given that no production code path mutated these rows
before this change, application-level mistakes — specifically the user-deletion
cascade — were the entire live risk.

If a genuine guarantee is ever needed, the next step up is a Postgres trigger
(plus a second statement-level trigger for `TRUNCATE`), and a non-superuser
application role. Both are infrastructure changes and belong on the role or the
table, in the spirit of `postgres-guard-rails.md`.

## The one deliberate exemption

`CreditUsageLog.is_refunded` stays writable. `SubscriptionService.refund_credits`
settles a usage log by flipping it, and reporting across `dashboard/`,
`classrooms/` and `billing/` filters on it. Freezing it would mean redesigning
refunds as reversing rows and rewriting every one of those queries. The
financial substance of the row — amount, feature, task, wallet, bucket,
timestamps — is frozen.

This is declared per-model as `mutable_fields`, so the exemption is explicit and
greppable rather than an accident.

## Making corrections

You cannot edit or delete a row. Record a **new** row that reverses or corrects
the old one — the accounting convention, and the reason `CreditLedgerType`
already has `REFUND`.

## The escape hatch

`billing.immutable.allow_unsafe_mutation()` is a thread-local context manager
that lifts enforcement for its duration.

```python
from billing.immutable import allow_unsafe_mutation

with allow_unsafe_mutation():
    CreditUsageLog.objects.filter(pk=log.pk).update(created_at=back_dated)
```

Legitimate callers are exactly two: test setup fabricating historical rows, and
a supervised data-repair session. It lifts the guard for **every** append-only
model while open, so keep the block as small as possible. Using it in ordinary
application code defeats the module and should be caught in review.

## Migration notes

`billing/0059` is **hand-written and must stay that way.** For the same model
change `makemigrations` generates:

```
- Remove field user from creditledger
+ Add field user_id to creditledger
```

That is `DROP COLUMN` then `ADD COLUMN`. It applies without error, the suite
still passes, and every existing row silently loses its attribution — the new
column arrives empty.

The ForeignKey `user` and the plain field `user_id` map to the **same** database
column (`user_id`), so the correct change is a state-only swap via
`SeparateDatabaseAndState` with empty `database_operations`. Operation #1 drops
the FK constraint and the `NOT NULL` first, while Django still understands the
field as a relation.

`billing/tests/test_audit_identity_migration.py` runs the real migration
executor and asserts the data survives. It has been verified to **fail** against
the naive generated version. If you ever regenerate `0059`, that test is what
will stop you shipping the data loss.

`billing/0060` backfills the snapshots in SQL, idempotently (`WHERE ... IS
NULL`), so it is safe to re-run and resumes cleanly after a partial run. Rows
whose user was **already** deleted keep their `user_id` and get a `NULL`
`user_email` — there is no surviving row to read an email from. That is
pre-existing loss being recorded, not loss this migration causes.

## Reporting caveat

`CreditUsageLog.wallet` is kept as a relation because ~20 reporting queries join
through it. Those joins are `INNER`, so a log whose wallet has been deleted is
**silently excluded** from them. When a query must count orphaned rows, filter
on `user_id` instead of joining through `wallet`.

The same applies to refunds: `SubscriptionService.refund_credits` selects
`select_related("wallet__user")`, so usage logs belonging to a deleted account
are skipped. That is the intended outcome — you cannot refund credits to an
account that no longer exists — but it is a behaviour worth knowing.
