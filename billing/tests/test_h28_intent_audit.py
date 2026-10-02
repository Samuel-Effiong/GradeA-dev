"""
billing/tests/test_h28_intent_audit.py
======================================
Epic A: every transition of a licence's Stripe-change intent to ESCALATED,
and every manual close, writes exactly one SUBSCRIPTION_CHANGE audit event
(SM ruling, option B).

  - The stale-intent check (Beat): SYSTEM, one event per intent it claims.
  - The flow's own escalation (`escalate`): the request's signed-in user, or
    SYSTEM when there is no request. The four view paths are tested in the
    phase modules, with each flow's own failure (test_h28_cancel_phases,
    _convert_phases, _seat_phases, _plan_phases).
  - resolve_licence_stripe_intent: the --by super admin, with the command's
    name; apply = 1 event, a lost race = 0, a dry run = 0.

The event is written in the same transaction as the status change, carries
ids and the two statuses only, and can never stop the change it records.
"""

from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import CommandError, call_command
from django.db import DatabaseError

from audit.enums import ActorRole
from audit.models import AuditEvent
from billing import license_stripe_mutation
from billing.models import LicenseStripeMutationIntent, LicenseStripeMutationStatus
from billing.tasks import escalate_stale_licence_stripe_intents
from billing.tests.test_h28_alerting import AlertingTestCase
from billing.tests.test_h28_cancel_phases import (
    MUTATION_LOGGER,
    as_a_request_by,
    assert_ids_only,
    assert_one_escalation_event,
    intent_events,
)

S = LicenseStripeMutationStatus
COMMAND = "resolve_licence_stripe_intent"
STALE = license_stripe_mutation.STALE_AFTER + timedelta(minutes=1)
NOTE = "Rolled forward by hand: Stripe has 12 seats, private note 7f3a"


class StaleIntentCheckAuditTests(AlertingTestCase):
    def test_each_escalated_intent_gets_one_system_event(self):
        pending = self.make_intent(S.PENDING, STALE)
        with self.assertLogs(MUTATION_LOGGER, level="ERROR"):
            self.assertEqual(license_stripe_mutation.escalate_stale_intents(), 1)

        pending.refresh_from_db()
        event = assert_one_escalation_event(self, pending, None)
        self.assertEqual(event.before, {"intent_status": "PENDING"})

        # A second run claims nothing and writes nothing.
        self.assertEqual(license_stripe_mutation.escalate_stale_intents(), 0)
        self.assertEqual(intent_events().count(), 1)

    def test_a_stale_stripe_applied_intent_records_what_it_was(self):
        applied = self.make_intent(S.STRIPE_APPLIED, STALE)
        with self.assertLogs(MUTATION_LOGGER, level="ERROR"):
            license_stripe_mutation.escalate_stale_intents()

        applied.refresh_from_db()
        event = assert_one_escalation_event(self, applied, None)
        self.assertEqual(event.before, {"intent_status": "STRIPE_APPLIED"})

    def test_the_beat_task_writes_the_event_as_system(self):
        pending = self.make_intent(S.PENDING, STALE)
        with self.assertLogs(level="ERROR"):
            escalate_stale_licence_stripe_intents.run()

        pending.refresh_from_db()
        assert_one_escalation_event(self, pending, None)

    def test_an_intent_the_check_does_not_claim_gets_no_event(self):
        """A flow that finishes between the read and the claim wins: the
        conditional update matches nothing, so there is nothing to record."""
        pending = self.make_intent(S.PENDING, STALE)
        real_filter = LicenseStripeMutationIntent.objects.filter

        def finished_meanwhile(*args, **kwargs):
            if "updated_at" in kwargs:
                LicenseStripeMutationIntent.objects.all().update(status=S.COMPLETE)
            return real_filter(*args, **kwargs)

        with patch.object(
            LicenseStripeMutationIntent.objects,
            "filter",
            side_effect=finished_meanwhile,
        ):
            self.assertEqual(license_stripe_mutation.escalate_stale_intents(), 0)

        self.assertEqual(self.status_of(pending), S.COMPLETE)
        self.assertEqual(intent_events().count(), 0)
        self.assertEqual(self.emails, [])

    def test_closed_and_recent_intents_get_no_event(self):
        self.make_intent(S.PENDING, timedelta(minutes=2))
        self.assertEqual(license_stripe_mutation.escalate_stale_intents(), 0)
        self.assertEqual(intent_events().count(), 0)


class InFlowEscalationAuditTests(AlertingTestCase):
    def test_with_no_request_the_event_is_system(self):
        """A direct service call: live QA today, or any later caller that
        has no signed-in user. (There is no webhook path: the only other
        caller of the flow, change_license_price, is called by tests only.)"""
        intent = self.make_intent(S.STRIPE_APPLIED)
        with self.assertLogs(MUTATION_LOGGER, level="ERROR"):
            license_stripe_mutation.escalate(intent, "the revert failed: timeout")

        assert_one_escalation_event(self, intent, None)

    def test_in_a_request_the_event_is_the_signed_in_users(self):
        intent = self.make_intent(S.PENDING)
        with as_a_request_by(self.superadmin):
            with self.assertLogs(MUTATION_LOGGER, level="ERROR"):
                license_stripe_mutation.escalate(intent, "private reason 9c1d")

        event = assert_one_escalation_event(self, intent, self.superadmin)
        self.assertEqual(event.before, {"intent_status": "PENDING"})
        self.assertNotIn("9c1d", f"{event.before}{event.after}{event.metadata}")

    def test_other_status_changes_write_no_event(self):
        """Only ESCALATED and a manual close are audited here; the licence's
        own history covers a change that completes."""
        for status in (S.STRIPE_APPLIED, S.COMPLETE, S.FAILED, S.COMPENSATED):
            with self.subTest(status=status):
                intent = self.make_intent(S.PENDING)
                self.assertTrue(license_stripe_mutation._set_status(intent, status))
                self.assertEqual(intent_events(intent).count(), 0)
                intent.delete()

    def test_a_status_that_is_not_saved_leaves_no_event(self):
        """Same transaction: if the status write fails, so does the event."""
        intent = self.make_intent(S.PENDING)
        with patch.object(
            LicenseStripeMutationIntent, "save", side_effect=DatabaseError("lost")
        ), self.assertLogs(MUTATION_LOGGER, level="ERROR"):
            license_stripe_mutation.escalate(intent, "why")

        self.assertEqual(self.status_of(intent), S.PENDING)
        self.assertEqual(intent_events().count(), 0)

    def test_a_failed_event_does_not_stop_the_escalation(self):
        """The audit table refusing the row must not cost the escalation or
        the alert: the status commits and the human is still told."""
        intent = self.make_intent(S.PENDING)
        with patch.object(
            AuditEvent.objects, "create", side_effect=DatabaseError("audit down")
        ), self.assertLogs(level="ERROR") as logs:
            license_stripe_mutation.escalate(intent, "why")

        self.assertEqual(self.status_of(intent), S.ESCALATED)
        self.assertEqual(intent_events().count(), 0)
        self.assertEqual(len(self.emails), 1)
        self.assertIn("MANUAL RECONCILIATION NEEDED", "\n".join(logs.output))

    def test_a_helper_that_raises_does_not_stop_the_escalation(self):
        """Anything else going wrong on the way to the emitter (here, the
        emitter itself raising) is caught in the helper's own savepoint."""
        intent = self.make_intent(S.PENDING)
        with patch(
            "audit.emitter.emit", side_effect=RuntimeError("emitter bug")
        ), self.assertLogs(MUTATION_LOGGER, level="ERROR") as logs:
            license_stripe_mutation.escalate(intent, "why")

        self.assertEqual(self.status_of(intent), S.ESCALATED)
        self.assertEqual(len(self.emails), 1)
        self.assertIn("Could not write the audit event", "\n".join(logs.output))


class ResolveCommandAuditTests(AlertingTestCase):
    def resolve(self, intent, *args):
        out = StringIO()
        call_command(COMMAND, str(intent.id), *args, stdout=out)
        return out.getvalue()

    def apply_args(self, outcome="applied"):
        return (
            "--apply",
            "--outcome",
            outcome,
            "--by",
            self.superadmin.email,
            "--note",
            NOTE,
        )

    def test_a_manual_close_writes_one_event_by_the_resolver(self):
        for outcome, status in (("applied", S.COMPLETE), ("not-applied", S.FAILED)):
            with self.subTest(outcome=outcome):
                intent = self.make_intent(S.ESCALATED)
                self.resolve(intent, *self.apply_args(outcome))

                intent.refresh_from_db()
                self.assertEqual(intent.status, status)
                [event] = list(intent_events(intent))
                self.assertEqual(event.actor_id, self.superadmin.id)
                self.assertEqual(event.actor_role, ActorRole.SUPER_ADMIN)
                self.assertEqual(event.before, {"intent_status": "ESCALATED"})
                self.assertEqual(event.after, {"intent_status": str(status)})
                self.assertEqual(
                    event.metadata,
                    {
                        "license_id": str(intent.license_subscription_id),
                        "command": COMMAND,
                    },
                )
                # The note stays on the intent; it never reaches the trail.
                self.assertEqual(intent.resolution_note, NOTE)
                assert_ids_only(self, intent, event)
                intent.delete()

    def test_closing_a_pending_intent_records_what_it_was(self):
        intent = self.make_intent(S.PENDING)
        self.resolve(intent, *self.apply_args())
        [event] = list(intent_events(intent))
        self.assertEqual(event.before, {"intent_status": "PENDING"})

    def test_a_dry_run_writes_no_event(self):
        intent = self.make_intent(S.ESCALATED)
        self.resolve(intent)
        self.assertEqual(self.status_of(intent), S.ESCALATED)
        self.assertEqual(intent_events().count(), 0)

    def test_a_lost_race_writes_no_event(self):
        """Something else closed it between the read and the update: the
        command refuses, and records nothing."""
        intent = self.make_intent(S.ESCALATED)
        real_filter = LicenseStripeMutationIntent.objects.filter

        def closed_meanwhile(*args, **kwargs):
            if "status" in kwargs:
                LicenseStripeMutationIntent.objects.all().update(status=S.COMPLETE)
            return real_filter(*args, **kwargs)

        with patch.object(
            LicenseStripeMutationIntent.objects, "filter", side_effect=closed_meanwhile
        ):
            with self.assertRaises(CommandError):
                self.resolve(intent, *self.apply_args("not-applied"))

        self.assertEqual(self.status_of(intent), S.COMPLETE)
        self.assertEqual(intent_events().count(), 0)

    def test_a_refused_apply_writes_no_event(self):
        intent = self.make_intent(S.ESCALATED)
        refusals = (
            ("--apply", "--outcome", "applied", "--note", NOTE),
            (
                "--apply",
                "--outcome",
                "applied",
                "--by",
                self.licence.admin_user.email,
                "--note",
                NOTE,
            ),
            ("--apply", "--outcome", "applied", "--by", self.superadmin.email),
        )
        for args in refusals:
            with self.subTest(args=args):
                with self.assertRaises(CommandError):
                    self.resolve(intent, *args)
        self.assertEqual(self.status_of(intent), S.ESCALATED)
        self.assertEqual(intent_events().count(), 0)

    def test_the_event_is_written_with_the_close_or_not_at_all(self):
        """Same transaction: if the event's write blows up past the helper,
        the close is not committed either. (The helper swallows its own
        failures, so this takes an error from the transaction itself.)"""
        intent = self.make_intent(S.ESCALATED)
        with patch(
            "billing.management.commands.resolve_licence_stripe_intent"
            ".audit_intent_status",
            side_effect=DatabaseError("connection lost"),
        ):
            with self.assertRaises(DatabaseError):
                self.resolve(intent, *self.apply_args())

        self.assertEqual(self.status_of(intent), S.ESCALATED)
        self.assertEqual(intent_events().count(), 0)
