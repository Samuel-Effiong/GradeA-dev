"""
Backfill the identity snapshots on existing audit rows.

Kept separate from the schema migration (0059) so a schema failure and a
data failure are never confused for each other, and so this can be
re-run on its own.

Done in SQL rather than through the ORM for two reasons: the row counts
are large (17,761 ledger rows on a single production user), and the
append-only guards in billing/immutable.py would reject a `.save()` or
`.update()` on these very rows. A migration writing history into place
is the one legitimate exception, and going through the database directly
expresses that plainly instead of smuggling it past the guard.

Both statements are idempotent - they touch only rows whose snapshot is
still NULL - so re-running is safe and a partial run resumes cleanly.

Rows whose user has ALREADY been deleted cannot be recovered: there is
no surviving row to read an email from. They keep their `user_id` and
get a NULL `user_email`. That is a pre-existing loss being recorded, not
one this migration causes.
"""

from django.db import migrations

BACKFILL_LEDGER = """
    UPDATE billing_creditledger AS cl
       SET user_email = u.email
      FROM users_customuser AS u
     WHERE cl.user_id = u.id
       AND cl.user_email IS NULL;
"""

BACKFILL_USAGE_LOG = """
    UPDATE billing_creditusagelog AS l
       SET user_id = w.user_id,
           user_email = u.email
      FROM billing_creditwallet AS w
      JOIN users_customuser AS u ON u.id = w.user_id
     WHERE l.wallet_id = w.id
       AND l.user_id IS NULL;
"""


def backfill(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(BACKFILL_LEDGER)
        ledger_rows = cursor.rowcount
        cursor.execute(BACKFILL_USAGE_LOG)
        usage_rows = cursor.rowcount

    print(
        f"\n  backfilled identity on {ledger_rows} ledger row(s) "
        f"and {usage_rows} usage log row(s)"
    )


def unbackfill(apps, schema_editor):
    """
    Deliberately a no-op. Reversing would erase attribution that 0059
    made it possible to keep, and the columns themselves are dropped by
    reversing 0059 anyway.
    """


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0059_append_only_audit_tables"),
    ]

    operations = [
        migrations.RunPython(backfill, unbackfill),
    ]
