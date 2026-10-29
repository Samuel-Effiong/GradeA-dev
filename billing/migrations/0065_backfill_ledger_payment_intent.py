"""
Populate and index `CreditLedger.stripe_payment_intent_id`.

WHY THE BACKFILL MATTERS
------------------------
The column is what attributes a refund or a chargeback back to the exact
credits a payment bought (billing/credit_reversal.py). Rows written from
0064 onwards fill it automatically in `CreditLedger.build()`, but every
overage purchase made BEFORE that carries its PaymentIntent only inside
the JSON `metadata`. Without this backfill, a refund of any historical
purchase would find no grant rows, conclude the payment bought nothing,
and leave the customer's credits in place — the precise defect the column
exists to fix, still present for every purchase already on the books.

The two purchase flows both write the key as
`metadata->>'stripe_payment_intent_id'`, so one statement covers both.

DONE IN SQL, ON PURPOSE
-----------------------
Same reasoning as 0060: `CreditLedger` is append-only, and the guards in
billing/immutable.py would reject a `.save()` or `.update()` on these
rows. A migration writing history into place is the one legitimate
exception, and going through the database directly says so plainly
instead of smuggling it past the guard.

Idempotent — it touches only rows whose column is still NULL and whose
metadata actually carries the key — so re-running is safe and a partial
run resumes cleanly.

CONCURRENTLY, ON PURPOSE
------------------------
`CreditLedger` is the fastest-growing table in the schema (17k+ rows for
a single teacher). A plain `CREATE INDEX` takes an ACCESS EXCLUSIVE lock
for the duration of the build, which here would block every credit
consumption in the product. `AddIndexConcurrently` requires the migration
NOT to run in a transaction — hence `atomic = False`.

Two consequences of `atomic = False` worth knowing before deploying:
  * the backfill is not rolled back if the index build afterwards fails;
    that is harmless, because the backfill is idempotent and re-running
    resumes;
  * a failed CONCURRENTLY build can leave an INVALID index behind. Check
    with:
        SELECT indexrelid::regclass FROM pg_index WHERE NOT indisvalid;
    and DROP any invalid index before re-running.
"""

from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models

BACKFILL = """
    UPDATE billing_creditledger
       SET stripe_payment_intent_id = metadata->>'stripe_payment_intent_id'
     WHERE stripe_payment_intent_id IS NULL
       AND metadata ? 'stripe_payment_intent_id'
       AND metadata->>'stripe_payment_intent_id' IS NOT NULL;
"""


def backfill(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(BACKFILL)
        rows = cursor.rowcount
    print(f"\n  attributed {rows} ledger row(s) to their Stripe payment")


def unbackfill(apps, schema_editor):
    """
    Deliberately a no-op.

    Clearing the column would silently disarm refund and chargeback
    reversal for every historical purchase, which is a worse state than
    the one this migration found. Reversing the schema is enough.
    """


class Migration(migrations.Migration):

    # Required by AddIndexConcurrently: CREATE INDEX CONCURRENTLY cannot
    # run inside a transaction block.
    atomic = False

    dependencies = [
        ("billing", "0064_refund_credit_reversal"),
    ]

    operations = [
        migrations.RunPython(backfill, unbackfill),
        AddIndexConcurrently(
            model_name="creditledger",
            index=models.Index(
                condition=models.Q(("stripe_payment_intent_id__isnull", False)),
                fields=["stripe_payment_intent_id"],
                name="bill_ledger_pi_idx",
            ),
        ),
    ]
