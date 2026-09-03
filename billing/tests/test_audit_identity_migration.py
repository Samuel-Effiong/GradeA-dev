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

BILLING_FROM = ("billing", "0058_alter_licensebillingrecord_record_type")
BILLING_TO = ("billing", "0060_backfill_audit_identity")

# The `users` app is pinned at whatever its CURRENT leaf is, in both
# targets, so only billing moves between them. That is what lets
# `old_apps.get_model("users", ...)` be a historical model that still
# knows every current column on users_customuser - so this test never
# needs to know which those are.
#
# The leaf is resolved from the migration graph at runtime, NOT written
# down here. Two earlier versions of this test failed for exactly that
# reason: the first hardcoded a raw INSERT column list and broke when
# `failed_login_attempts` was added; the second hardcoded the migration
# NAME ("0036_customuser_failed_login_attempts_and_more"), which passed
# locally and then died in CI with NodeNotFoundError, because that
# migration was still an uncommitted local file. Reading the graph is
# the only version that cannot drift from the repository.


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
        executor.migrate(self._targets(executor.loader, BILLING_TO))
        super().tearDown()

    def _insert_user(self, apps, email):
        """
        Built from the HISTORICAL users model, not the live one.

        The historical class carries no custom save(), no manager
        overrides and fires no registration signals - which matters here,
        because those signals activate a free trial and would write a
        CreditLedger row through the CURRENT model while billing is still
        at 0058, referencing columns that do not exist yet.

        Field defaults still apply, so new NOT NULL columns on
        users_customuser are populated automatically and this test does
        not have to be updated every time one is added.
        """
        CustomUser = apps.get_model("users", "CustomUser")
        user = CustomUser.objects.create(
            id=uuid.uuid4(),
            email=email,
            password="",  # pragma: allowlist secret
            is_active=True,
            date_joined=timezone.now(),
        )
        return user.id

    def _targets(self, loader, billing_target):
        """
        `billing_target` plus the CURRENT leaf of `users`, read from the
        migration graph so it can never disagree with what is actually
        in the repository.
        """
        leaves = list(loader.graph.leaf_nodes("users"))
        if len(leaves) != 1:
            self.fail(
                f"expected exactly one leaf migration for `users`, got {leaves!r}. "
                "A merge migration is probably needed before this test can pin it."
            )
        return [billing_target, leaves[0]]

    def _migrate(self, billing_target):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        targets = self._targets(executor.loader, billing_target)
        executor.migrate(targets)
        return executor.loader.project_state(targets).apps

    def test_user_id_survives_and_email_is_backfilled(self):
        old_apps = self._migrate(BILLING_FROM)

        # --- Arrange, at the OLD schema: `user` is still a ForeignKey.
        CreditWallet = old_apps.get_model("billing", "CreditWallet")
        CreditBucket = old_apps.get_model("billing", "CreditBucket")
        CreditLedger = old_apps.get_model("billing", "CreditLedger")
        CreditUsageLog = old_apps.get_model("billing", "CreditUsageLog")

        user_id = self._insert_user(old_apps, "pre-migration@example.com")
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
        new_apps = self._migrate(BILLING_TO)

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
        old_apps = self._migrate(BILLING_FROM)

        CreditWallet = old_apps.get_model("billing", "CreditWallet")
        CreditBucket = old_apps.get_model("billing", "CreditBucket")
        CreditLedger = old_apps.get_model("billing", "CreditLedger")

        user_id = self._insert_user(old_apps, "idempotent@example.com")
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

        self._migrate(BILLING_TO)
        self._migrate(BILLING_FROM)
        new_apps = self._migrate(BILLING_TO)

        NewLedger = new_apps.get_model("billing", "CreditLedger")
        self.assertEqual(
            NewLedger.objects.get(pk=ledger.pk).user_email,
            "idempotent@example.com",
        )
