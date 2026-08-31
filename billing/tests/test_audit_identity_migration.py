"""
billing/tests/test_audit_identity_migration.py
==============================================
Proves that migration 0059 PRESERVES `billing_creditledger.user_id`
instead of dropping and recreating it, and that 0060 backfills the
email snapshots.

WHY THIS TEST EXISTS
--------------------
`makemigrations` generates this for the same model change:

    - Remove field user from creditledger
    + Add field user_id to creditledger

That is a DROP COLUMN followed by an ADD COLUMN. It migrates without
error, every other test in the suite still passes, and on production it
silently discards the attribution of 17,761 ledger rows - because the
new column arrives empty. Nothing about the schema afterwards looks
wrong; the data is simply gone.

0059 is therefore hand-written, swapping the ForeignKey for a plain
UUID field in Django's STATE only, since both map to the same `user_id`
column. This test is the thing standing between that reasoning and a
future `makemigrations` run quietly replacing it.

It runs the real migration executor against real tables rather than
asserting on the migration file's contents, because the failure being
guarded against is behavioural: the wrong migration is still a valid
migration.
"""

import uuid

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone

MIGRATE_FROM = [("billing", "0058_alter_licensebillingrecord_record_type")]
MIGRATE_TO = [("billing", "0060_backfill_audit_identity")]


class AuditIdentityMigrationTests(TransactionTestCase):
    """
    TransactionTestCase because the migration executor issues DDL, which
    cannot run inside the outer transaction TestCase wraps around tests.
    """

    available_apps = None

    def tearDown(self):
        # Leave the database fully migrated again, or every test that
        # runs after this one in the same process sees a stale schema.
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(MIGRATE_TO)
        super().tearDown()

    def _insert_user(self, email):
        """
        Raw SQL on purpose. The historical `users` state at billing/0058
        predates `registration_method`, so the historical model cannot
        populate a column the live table still declares NOT NULL. The
        users table is untouched by these migrations, so writing it
        directly is both safe and simpler than reconciling the states.
        """
        user_id = uuid.uuid4()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO users_customuser (
                    id, password, is_superuser, first_name, last_name,
                    is_staff, is_active, date_joined, email, user_type,
                    registration_method
                ) VALUES (%s, '', false, '', '', false, true, %s, %s,
                          'TEACHER', 'EMAIL')
                """,
                [user_id, timezone.now(), email],
            )
        return user_id

    def _migrate(self, targets):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(targets)
        return executor.loader.project_state(targets).apps

    def test_user_id_survives_and_email_is_backfilled(self):
        old_apps = self._migrate(MIGRATE_FROM)

        # --- Arrange, at the OLD schema: `user` is still a ForeignKey.
        CreditWallet = old_apps.get_model("billing", "CreditWallet")
        CreditBucket = old_apps.get_model("billing", "CreditBucket")
        CreditLedger = old_apps.get_model("billing", "CreditLedger")
        CreditUsageLog = old_apps.get_model("billing", "CreditUsageLog")

        user_id = self._insert_user("pre-migration@example.com")
        wallet = CreditWallet.objects.create(id=uuid.uuid4(), user_id=user_id)
        bucket = CreditBucket.objects.create(
            id=uuid.uuid4(),
            wallet=wallet,
            bucket_type="MONTHLY",
            total_credits=100,
            used_credits=0,
        )
        ledger = CreditLedger.objects.create(
            id=uuid.uuid4(),
            user_id=user_id,
            bucket=bucket,
            ledger_type="GRANT",
            amount=100,
            reference="pre-migration grant",
        )
        usage = CreditUsageLog.objects.create(
            id=uuid.uuid4(),
            wallet=wallet,
            bucket=bucket,
            amount=10,
            feature="Grading Assignment",
        )

        # --- Act.
        new_apps = self._migrate(MIGRATE_TO)

        # --- Assert, at the NEW schema.
        NewLedger = new_apps.get_model("billing", "CreditLedger")
        NewUsage = new_apps.get_model("billing", "CreditUsageLog")

        migrated = NewLedger.objects.get(pk=ledger.pk)
        self.assertEqual(
            migrated.user_id,
            user_id,
            "0059 dropped and recreated user_id instead of preserving it — "
            "this is the silent data loss the hand-written migration exists "
            "to prevent.",
        )
        self.assertEqual(migrated.user_email, "pre-migration@example.com")

        migrated_usage = NewUsage.objects.get(pk=usage.pk)
        self.assertEqual(migrated_usage.user_id, user_id)
        self.assertEqual(migrated_usage.user_email, "pre-migration@example.com")

    def test_backfill_is_idempotent(self):
        """0060 is re-runnable: a second pass must not overwrite or fail."""
        old_apps = self._migrate(MIGRATE_FROM)

        CreditWallet = old_apps.get_model("billing", "CreditWallet")
        CreditBucket = old_apps.get_model("billing", "CreditBucket")
        CreditLedger = old_apps.get_model("billing", "CreditLedger")

        user_id = self._insert_user("idempotent@example.com")
        wallet = CreditWallet.objects.create(id=uuid.uuid4(), user_id=user_id)
        bucket = CreditBucket.objects.create(
            id=uuid.uuid4(),
            wallet=wallet,
            bucket_type="MONTHLY",
            total_credits=100,
            used_credits=0,
        )
        ledger = CreditLedger.objects.create(
            id=uuid.uuid4(),
            user_id=user_id,
            bucket=bucket,
            ledger_type="GRANT",
            amount=100,
        )

        self._migrate(MIGRATE_TO)
        self._migrate(MIGRATE_FROM)
        new_apps = self._migrate(MIGRATE_TO)

        NewLedger = new_apps.get_model("billing", "CreditLedger")
        self.assertEqual(
            NewLedger.objects.get(pk=ledger.pk).user_email,
            "idempotent@example.com",
        )
