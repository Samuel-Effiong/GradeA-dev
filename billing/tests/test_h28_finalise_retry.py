"""
billing/tests/test_h28_finalise_retry.py
========================================
H-28 Change 1, commit 7: the bounded retry of the local write (phase D)
after Stripe has applied a change (DESIGN_PROPOSAL.md §9e).

The NAMED GATE-5 ASSERTION (d4's wording): after the idle-in-transaction
kill, the retry discards the dead connection, opens a fresh transaction
outside the failed atomic block, and the injection proves the retry
SUCCEEDS on the fresh connection — not merely that a retry happened.
`test_after_the_connection_is_killed_the_retry_succeeds_on_a_fresh_one`
is that assertion: it compares Postgres backend pids.

Also: the retry is bounded, only transient database errors are retried,
and a retry after a commit whose reply was lost writes nothing twice.

Driven through cancel_license_subscription, the simplest flow on the
phases; the retry is shared by every flow (license_stripe_mutation.finalise).
"""

import threading
from unittest.mock import patch

from django.db import IntegrityError, OperationalError, connection

from billing import license_stripe_mutation
from billing.imports import stripe
from billing.license_service import LicenseSubscriptionService
from billing.models import (
    LicenseBillingRecord,
    LicenseBillingRecordType,
    LicenseStripeMutationIntent,
    LicenseStripeMutationStatus,
)
from billing.tests.test_h28_cancel_phases import LicencePhaseTestCase


def backend_pid():
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_backend_pid()")
        return cursor.fetchone()[0]


class _KillTheConnectionOnce:
    """
    Postgres terminating the session, as the 60 s idle-in-transaction
    timeout does. Armed by the test once Stripe has applied the change; it
    fires on the next row lock, which is phase D's re-lock of the licence
    (phase C's intent update takes none). The server really ends the
    session, and the statement then fails on the dead connection exactly as
    it would in production: Django raises its own OperationalError.
    """

    def __init__(self):
        self.armed = False
        self.killed_pid = None
        self.killed_db = None

    def __call__(self, execute, sql, params, many, context):
        if self.armed and "FOR UPDATE" in sql:
            self.armed = False
            # Only ever this very session: pg_backend_pid() on the connection
            # being killed, never a pid looked up from pg_stat_activity, since
            # other sessions' test databases share this Postgres server.
            self.killed_pid, self.killed_db = session_of(context["connection"])
            try:
                with context["connection"].connection.cursor() as raw:
                    raw.execute("SELECT pg_terminate_backend(pg_backend_pid())")
            except Exception:  # noqa: BLE001 - the session ending is the point
                pass
        return execute(sql, params, many, context)


def session_of(wrapper):
    """(backend pid, database) of the connection itself."""
    with wrapper.connection.cursor() as raw:
        raw.execute("SELECT pg_backend_pid(), current_database()")
        return tuple(raw.fetchone())


class FinaliseRetryTests(LicencePhaseTestCase):
    SUB_ID = "sub_h28_retry"

    def setUp(self):
        super().setUp()
        self.sleeps = []
        p = patch.object(
            license_stripe_mutation.time, "sleep", side_effect=self.sleeps.append
        )
        p.start()
        self.addCleanup(p.stop)

    def cancel(self):
        return LicenseSubscriptionService.cancel_license_subscription(
            self.licence, performed_by=self.superadmin
        )

    def cancellation_records(self):
        return LicenseBillingRecord.objects.filter(
            license_subscription=self.licence,
            record_type=LicenseBillingRecordType.CANCELLED,
        ).count()

    def _arm_after_stripe(self, arm):
        """Patch the Stripe call so `arm()` runs once it has applied."""

        def apply_then_arm(*args, **kwargs):
            result = self.stripe.subscription_modify(*args, **kwargs)
            if kwargs.get("cancel_at_period_end") is True:
                arm()
            return result

        return patch.object(stripe.Subscription, "modify", side_effect=apply_then_arm)

    # -- the named Gate-5 assertion ------------------------------------------

    def test_after_the_connection_is_killed_the_retry_succeeds_on_a_fresh_one(self):
        kill = _KillTheConnectionOnce()

        with self._arm_after_stripe(lambda: setattr(kill, "armed", True)):
            with connection.execute_wrapper(kill):
                updated = self.cancel()

        self.assertIsNotNone(kill.killed_pid, "the kill never fired in phase D")
        # The session it ended was this test's own, on its own database.
        self.assertEqual(kill.killed_db, connection.settings_dict["NAME"])
        self.assertTrue(str(kill.killed_db).startswith("test_"), kill.killed_db)
        self.assertNotEqual(
            backend_pid(),
            kill.killed_pid,
            "the retry did not run on a fresh connection",
        )
        # The retry SUCCEEDED: the local write committed, the intent is
        # COMPLETE, and Stripe was changed once and never reverted.
        self.assertFalse(updated.auto_renew)
        self.assertFalse(self.fresh_licence().auto_renew)
        self.assertEqual(self.cancellation_records(), 1)
        self.assertEqual(
            self.only_intent().status, LicenseStripeMutationStatus.COMPLETE
        )
        self.assertEqual(len(self.modify_calls()), 1)
        self.assertTrue(self.stripe.cancel_at_period_end)
        self.assertEqual(self.sleeps, [0.2])

    # -- bounded, and transient errors only ----------------------------------

    def test_a_persistent_transient_failure_stops_after_three_attempts(self):
        attempts = []

        def always_drop(*args, **kwargs):
            attempts.append(1)
            raise OperationalError("server closed the connection unexpectedly")

        with patch.object(
            LicenseBillingRecord.objects, "create", side_effect=always_drop
        ):
            with self.assertRaises(
                license_stripe_mutation.LicenceStripeChangeNotRecorded
            ):
                self.cancel()

        self.assertEqual(len(attempts), license_stripe_mutation.FINALISE_ATTEMPTS)
        self.assertEqual(self.sleeps, [0.2, 1.0])
        # Then the normal failure path: no money moved, so it is undone.
        self.assertEqual(
            self.only_intent().status, LicenseStripeMutationStatus.COMPENSATED
        )
        self.assertFalse(self.stripe.cancel_at_period_end)

    def test_a_logic_failure_is_not_retried(self):
        attempts = []

        def constraint(*args, **kwargs):
            attempts.append(1)
            raise IntegrityError("duplicate key value violates unique constraint")

        with patch.object(
            LicenseBillingRecord.objects, "create", side_effect=constraint
        ):
            with self.assertRaises(
                license_stripe_mutation.LicenceStripeChangeNotRecorded
            ):
                self.cancel()

        self.assertEqual(len(attempts), 1)
        self.assertEqual(self.sleeps, [])
        self.assertEqual(
            self.only_intent().status, LicenseStripeMutationStatus.COMPENSATED
        )

    def test_a_retry_after_a_commit_whose_reply_was_lost_writes_nothing_twice(self):
        """The first attempt's commit landed but the connection died before
        its reply arrived. Modelled by committing COMPLETE from another
        connection and then failing the first attempt: the retry must see
        COMPLETE and write nothing more."""
        failed_once = []
        real_create = LicenseBillingRecord.objects.create

        def complete_elsewhere_then_drop(*args, **kwargs):
            if failed_once:
                return real_create(*args, **kwargs)
            failed_once.append(1)

            def commit_complete():
                LicenseStripeMutationIntent.objects.update(
                    status=LicenseStripeMutationStatus.COMPLETE
                )
                connection.close()

            t = threading.Thread(target=commit_complete)
            t.start()
            t.join(10)
            raise OperationalError("server closed the connection unexpectedly")

        with patch.object(
            LicenseBillingRecord.objects,
            "create",
            side_effect=complete_elsewhere_then_drop,
        ):
            self.cancel()

        self.assertEqual(failed_once, [1])
        self.assertEqual(self.cancellation_records(), 0, "the retry wrote again")
        self.assertEqual(
            self.only_intent().status, LicenseStripeMutationStatus.COMPLETE
        )
        self.assertEqual(len(self.modify_calls()), 1, "nothing may be reverted")
