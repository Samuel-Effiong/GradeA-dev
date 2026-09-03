"""
Sever every cascading/nulling relation into the two financial audit
tables, and give them identity that survives the account.

THE CRITICAL OPERATION IS #2, AND IT IS HAND-WRITTEN ON PURPOSE.

`makemigrations` generates this for the same model change:

    - Remove field user from creditledger
    + Add field user_id to creditledger

which DROPS the `user_id` column and adds a fresh, empty one. On
production that is 17,761 ledger rows losing their attribution
irrecoverably (measured with Django's deletion collector, read-only).

The FK `user` and the plain field `user_id` both map to the SAME
database column, `user_id` - a ForeignKey stores its value in
`<name>_id`. So the swap is purely a state change: tell Django the
column is now a plain UUID rather than a relation, and touch no data.
`SeparateDatabaseAndState` with an empty `database_operations` is how
that is expressed.

Operation #1 must run first and separately: it drops the actual FK
constraint (and the NOT NULL) while the field is still a relation
Django understands. Only then is the state swap safe.

Backfill of the new snapshot columns is migration 0060, kept separate so
a schema failure and a data failure cannot be confused for each other.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0058_alter_licensebillingrecord_record_type"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # 1. Drop the FK constraint and the NOT NULL, keeping the column.
        migrations.AlterField(
            model_name="creditledger",
            name="user",
            field=models.ForeignKey(
                blank=True,
                db_constraint=False,
                null=True,
                on_delete=django.db.models.deletion.DO_NOTHING,
                related_name="credit_ledgers",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        # 2. Reinterpret that same column as a plain UUID. State only.
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(
                    model_name="creditledger",
                    name="user",
                ),
                migrations.AddField(
                    model_name="creditledger",
                    name="user_id",
                    field=models.UUIDField(
                        blank=True,
                        db_index=True,
                        null=True,
                        help_text=(
                            "ID of the user this entry belongs to, stored as "
                            "a plain value. Deliberately NOT a ForeignKey: "
                            "the entry must survive deletion of the account. "
                            "May reference a user that no longer exists."
                        ),
                    ),
                ),
            ],
            database_operations=[],
        ),
        # 3. The identity snapshots (genuinely new columns).
        migrations.AddField(
            model_name="creditledger",
            name="user_email",
            field=models.CharField(
                blank=True,
                max_length=254,
                null=True,
                help_text=(
                    "The user's email captured at write time — the only "
                    "human-readable attribution left once the account is "
                    "gone. A snapshot, deliberately not derived by joining "
                    "to the live user row."
                ),
            ),
        ),
        migrations.AddField(
            model_name="creditusagelog",
            name="user_id",
            field=models.UUIDField(
                blank=True,
                db_index=True,
                null=True,
                help_text=(
                    "ID of the billed user, stored as a plain value. "
                    "Previously reachable only by joining through `wallet`, "
                    "which meant the log died with the wallet and the "
                    "account."
                ),
            ),
        ),
        migrations.AddField(
            model_name="creditusagelog",
            name="user_email",
            field=models.CharField(
                blank=True,
                max_length=254,
                null=True,
                help_text=(
                    "The billed user's email captured at write time — a "
                    "snapshot, deliberately not derived by joining to the "
                    "live user row."
                ),
            ),
        ),
        # 4. Every remaining relation into the audit tables stops
        #    cascading (which deleted rows) and stops nulling (which
        #    UPDATEd them - itself a mutation of an immutable record).
        migrations.AlterField(
            model_name="creditledger",
            name="bucket",
            field=models.ForeignKey(
                db_constraint=False,
                null=True,
                on_delete=django.db.models.deletion.DO_NOTHING,
                related_name="credit_ledgers",
                to="billing.creditbucket",
                help_text=(
                    "Credit bucket the ledger is associated with. "
                    "DO_NOTHING with no database constraint: the previous "
                    "SET_NULL did not delete the row but did UPDATE it, "
                    "which is itself a mutation of an immutable record. The "
                    "id is retained even once the bucket is gone."
                ),
            ),
        ),
        migrations.AlterField(
            model_name="creditusagelog",
            name="wallet",
            field=models.ForeignKey(
                db_constraint=False,
                on_delete=django.db.models.deletion.DO_NOTHING,
                related_name="credit_usage_logs",
                to="billing.creditwallet",
                help_text=(
                    "Credit wallet the usage log is associated with. "
                    "DO_NOTHING with no database constraint so deleting a "
                    "wallet (or the user above it) can no longer cascade "
                    "this audit row away. The relation is kept — rather "
                    "than reduced to a bare id — because reporting joins "
                    "through it; use `user_id` when the row may be "
                    "orphaned, since a join drops orphans silently."
                ),
            ),
        ),
        migrations.AlterField(
            model_name="creditusagelog",
            name="bucket",
            field=models.ForeignKey(
                db_constraint=False,
                on_delete=django.db.models.deletion.DO_NOTHING,
                related_name="credit_usage_logs",
                to="billing.creditbucket",
                help_text="Credit bucket the usage log is associated with",
            ),
        ),
        migrations.AlterField(
            model_name="creditusagelog",
            name="course",
            field=models.ForeignKey(
                blank=True,
                db_constraint=False,
                null=True,
                on_delete=django.db.models.deletion.DO_NOTHING,
                related_name="credit_usage_logs",
                to="classrooms.course",
                help_text=(
                    "Course the consuming task was performed under, when "
                    "known. Null for tasks with no course context (e.g. "
                    "custom AI chat, school-wide summaries) or for usage "
                    "logged before this field existed."
                ),
            ),
        ),
        migrations.AlterField(
            model_name="creditusagelog",
            name="school",
            field=models.ForeignKey(
                blank=True,
                db_constraint=False,
                null=True,
                on_delete=django.db.models.deletion.DO_NOTHING,
                related_name="credit_usage_logs",
                to="classrooms.school",
                help_text=(
                    "The school the billed user (teacher or school admin) belonged "
                    "to at the moment these credits were consumed — a snapshot, "
                    "deliberately NOT derived by joining to the user's current "
                    "`school` FK. That field is mutable (a teacher can be "
                    "reassigned to a different school after the fact), so joining "
                    "live would retroactively misattribute historical usage to "
                    "whichever school the user happens to belong to today. Null "
                    "if the user had no school at consumption time (e.g. an "
                    "individual, non-license teacher), or for usage logged before "
                    "this field existed."
                ),
            ),
        ),
    ]
