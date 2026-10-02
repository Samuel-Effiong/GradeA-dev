"""
Composite (owner, -created_at) indexes on the two fastest-growing audit
tables.

WHY
---
`CreditLedgerViewSet` and `CreditUsageLogViewSet` both scope to the owner
and order by `-created_at`, but the tables only carried standalone
`user_id` / `wallet_id` indexes. Measured with EXPLAIN ANALYZE at ~100k
rows: the planner used a Bitmap Heap Scan on the owner column feeding a
`top-N heapsort` — it read EVERY row the user owns in order to return a
page of 20 (250 rows read for 20 returned in the benchmark). That cost
grows linearly with a user's history, on the two tables that grow fastest
and are never pruned.

`BillingTransaction` already has the equivalent `(user_id, occurred_at)`
and `(school_id, occurred_at)` indexes; these two were simply missed.

CONCURRENTLY, ON PURPOSE
------------------------
A plain `CREATE INDEX` takes an ACCESS EXCLUSIVE lock for the duration of
the build, which on an append-only financial table means blocking every
credit consumption in the product while it runs. `AddIndexConcurrently`
builds without blocking writes, at the cost of a second table pass and the
requirement that the migration NOT run inside a transaction — hence
`atomic = False`.

Two consequences of `atomic = False` worth knowing before deploying:
  * if this migration fails partway, the successfully-created indexes stay;
    re-running is safe because each operation is reversible and Django
    records them individually;
  * a failed CONCURRENTLY build can leave an INVALID index behind. Check
    with:
        SELECT indexrelid::regclass FROM pg_index WHERE NOT indisvalid;
    and DROP any invalid index before re-running.
"""

from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):

    # Required by AddIndexConcurrently: CREATE INDEX CONCURRENTLY cannot
    # run inside a transaction block.
    atomic = False

    dependencies = [
        ("billing", "0060_backfill_audit_identity"),
        ("classrooms", "0016_school_is_active"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="creditledger",
            index=models.Index(
                fields=["user_id", "-created_at"], name="bill_ledger_user_t_idx"
            ),
        ),
        AddIndexConcurrently(
            model_name="creditusagelog",
            index=models.Index(
                fields=["wallet", "-created_at"], name="bill_usagelog_wallet_t_idx"
            ),
        ),
        AddIndexConcurrently(
            model_name="creditusagelog",
            index=models.Index(
                fields=["user_id", "-created_at"], name="bill_usagelog_user_t_idx"
            ),
        ),
    ]
