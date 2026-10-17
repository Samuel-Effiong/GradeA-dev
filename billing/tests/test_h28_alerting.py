"""
billing/tests/test_h28_alerting.py
==================================
H-28 Change 1, commit 8: a human is TOLD (DESIGN_PROPOSAL.md §9g–§9i).

  - Every "manual reconciliation needed" emails every active super admin,
    best effort: an alert that fails never raises into the flow.
  - The stale-intent check (§9i (1)) escalates an intent a killed worker
    left PENDING or STRIPE_APPLIED, and alerts, once. One query, no Stripe
    call.
  - resolve_licence_stripe_intent (§9h-bis, the SM's ruling) closes an
    intent and frees the licence: dry run by default; --apply needs an
    outcome, a super admin and a note; it never calls Stripe and never
    changes the licence.
"""

from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.conf import settings
from django.core.management import CommandError, call_command
from django.utils import timezone

from billing import license_stripe_mutation
from billing.models import (
    LicenseStripeMutationIntent,
    LicenseStripeMutationOperation,
    LicenseStripeMutationStatus,
)
from billing.tasks import escalate_stale_licence_stripe_intents
from billing.tests.test_h28_cancel_phases import MUTATION_LOGGER, LicencePhaseTestCase
from users.models import CustomUser, UserTypes

S = LicenseStripeMutationStatus


def licence_row(licence):
    """Every concrete column of a licence except updated_at."""
    return {
        field.attname: getattr(licence, field.attname)
        for field in type(licence)._meta.concrete_fields
        if field.name != "updated_at"
    }


class AlertingTestCase(LicencePhaseTestCase):
    SUB_ID = "sub_h28_alerts"

    def setUp(self):
        super().setUp()
        self.emails = []
        p = patch("AutoGrader.dispatch.safe_delay", side_effect=self._queue)
        p.start()
        self.addCleanup(p.stop)

    def _queue(self, task, **kwargs):
        self.emails.append(kwargs)

    def make_intent(self, status=S.PENDING, age=None):
        intent = LicenseStripeMutationIntent.objects.create(
            license_subscription=self.licence,
            operation=LicenseStripeMutationOperation.UPDATE_SEATS,
            stripe_subscription_id=self.SUB_ID,
            requested_change={"old_max_seats": 10, "new_max_seats": 12},
            status=status,
        )
        if age:
            LicenseStripeMutationIntent.objects.filter(pk=intent.pk).update(
                updated_at=timezone.now() - age
            )
        return LicenseStripeMutationIntent.objects.get(pk=intent.pk)

    def status_of(self, intent):
        return LicenseStripeMutationIntent.objects.get(pk=intent.pk).status


class AlertTests(AlertingTestCase):
    def test_an_escalation_emails_every_active_super_admin(self):
        CustomUser.objects.create_superuser(
            email="second@h28-alerts.gradea.com",
            password="test123",  # pragma: allowlist secret
            user_type=UserTypes.SUPER_ADMIN,
        )
        CustomUser.objects.create_superuser(
            email="gone@h28-alerts.gradea.com",
            password="test123",  # pragma: allowlist secret
            user_type=UserTypes.SUPER_ADMIN,
            is_active=False,
        )
        intent = self.make_intent()

        with self.assertLogs(MUTATION_LOGGER, level="ERROR"):
            license_stripe_mutation.escalate(intent, "the revert failed: timeout")

        recipients = sorted(e["recipient_list"][0] for e in self.emails)
        self.assertEqual(
            recipients,
            sorted([self.superadmin.email, "second@h28-alerts.gradea.com"]),
        )
        for email in self.emails:
            self.assertIn(str(intent.id), email["message"])
            self.assertIn("the revert failed: timeout", email["message"])
            self.assertIn("resolve_licence_stripe_intent", email["message"])
            self.assertEqual(email["from_email"], settings.DEFAULT_FROM_EMAIL)
        self.assertEqual(self.status_of(intent), S.ESCALATED)

    def test_a_failing_alert_never_raises(self):
        intent = self.make_intent()

        with patch(
            "AutoGrader.dispatch.safe_delay", side_effect=RuntimeError("broken")
        ), self.assertLogs(MUTATION_LOGGER, level="ERROR") as logs:
            license_stripe_mutation.escalate(intent, "why")

        self.assertEqual(self.status_of(intent), S.ESCALATED)
        self.assertIn("MANUAL RECONCILIATION NEEDED", "\n".join(logs.output))


class StaleIntentCheckTests(AlertingTestCase):
    STALE = license_stripe_mutation.STALE_AFTER + timedelta(minutes=1)

    def test_stale_in_flight_intents_are_escalated_and_alerted_once(self):
        pending = self.make_intent(S.PENDING, self.STALE)
        with self.assertLogs(MUTATION_LOGGER, level="ERROR") as logs:
            self.assertEqual(license_stripe_mutation.escalate_stale_intents(), 1)

        pending.refresh_from_db()
        self.assertEqual(pending.status, S.ESCALATED)
        self.assertIsNotNone(pending.escalated_at)
        self.assertIn("outcome at Stripe is unknown", pending.failure_reason)
        self.assertIn(str(pending.id), "\n".join(logs.output))
        self.assertEqual(len(self.emails), 1)

        # ESCALATED is not picked up again: one alert per abandoned intent.
        self.assertEqual(license_stripe_mutation.escalate_stale_intents(), 0)
        self.assertEqual(len(self.emails), 1)

    def test_an_old_escalated_intent_is_not_alerted_again(self):
        """ESCALATED already told a human; however long it waits for one,
        the check never re-selects it."""
        old = self.make_intent(S.ESCALATED, self.STALE)

        self.assertEqual(license_stripe_mutation.escalate_stale_intents(), 0)

        self.assertEqual(self.status_of(old), S.ESCALATED)
        self.assertEqual(self.emails, [])

    def test_a_stale_stripe_applied_intent_is_escalated(self):
        applied = self.make_intent(S.STRIPE_APPLIED, self.STALE)
        with self.assertLogs(MUTATION_LOGGER, level="ERROR"):
            license_stripe_mutation.escalate_stale_intents()
        applied.refresh_from_db()
        self.assertEqual(applied.status, S.ESCALATED)
        self.assertIn("never recorded it", applied.failure_reason)

    def test_recent_and_closed_intents_are_left_alone(self):
        recent = self.make_intent(S.PENDING, timedelta(minutes=2))

        self.assertEqual(license_stripe_mutation.escalate_stale_intents(), 0)
        self.assertEqual(self.status_of(recent), S.PENDING)

        LicenseStripeMutationIntent.objects.all().delete()
        for status in (S.COMPLETE, S.FAILED, S.COMPENSATED):
            old = self.make_intent(status, self.STALE)
            self.assertEqual(license_stripe_mutation.escalate_stale_intents(), 0)
            self.assertEqual(self.status_of(old), status)
            old.delete()
        self.assertEqual(self.emails, [])

    def test_the_check_makes_no_stripe_call(self):
        self.make_intent(S.PENDING, self.STALE)
        with self.assertLogs(MUTATION_LOGGER, level="ERROR"):
            license_stripe_mutation.escalate_stale_intents()
        self.assertEqual(self.stripe.calls, [])

    def test_the_periodic_task_is_scheduled_and_watched(self):
        entry = settings.CELERY_BEAT_SCHEDULE["escalate-stale-licence-stripe-intents"]
        self.assertEqual(
            entry["task"], "billing.tasks.escalate_stale_licence_stripe_intents"
        )
        self.assertIn(
            "escalate-stale-licence-stripe-intents", settings.BEAT_HEALTH_EXPECTATIONS
        )
        self.make_intent(S.PENDING, self.STALE)
        with self.assertLogs(level="ERROR"):
            summary = escalate_stale_licence_stripe_intents.run()
        self.assertEqual(summary, "Stale licence Stripe intents escalated: 1")


class ResolveCommandTests(AlertingTestCase):
    def resolve(self, intent, *args):
        out = StringIO()
        call_command("resolve_licence_stripe_intent", str(intent.id), *args, stdout=out)
        return out.getvalue()

    def test_a_dry_run_describes_and_changes_nothing(self):
        intent = self.make_intent(S.ESCALATED)

        out = self.resolve(intent)

        self.assertIn(str(intent.id), out)
        self.assertIn("UPDATE_SEATS", out)
        self.assertIn('"new_max_seats": 12', out)
        self.assertIn("Dry run: nothing changed", out)
        self.assertEqual(self.status_of(intent), S.ESCALATED)

    def test_apply_needs_an_outcome_a_note_and_a_super_admin(self):
        intent = self.make_intent(S.ESCALATED)
        by = ("--by", self.superadmin.email)

        for args, message in (
            (("--apply", "--note", "x", *by), "--outcome"),
            (("--apply", "--outcome", "applied", *by), "--note"),
            (("--apply", "--outcome", "applied", "--note", "   ", *by), "--note"),
            (("--apply", "--outcome", "applied", "--note", "x"), "--by"),
            (
                (
                    "--apply",
                    "--outcome",
                    "applied",
                    "--note",
                    "x",
                    "--by",
                    "nobody@x.test",
                ),
                "not an active super admin",
            ),
        ):
            with self.subTest(args=args), self.assertRaisesRegex(CommandError, message):
                self.resolve(intent, *args)
        self.assertEqual(self.status_of(intent), S.ESCALATED)

    def test_apply_closes_the_intent_and_frees_the_licence(self):
        intent = self.make_intent(S.ESCALATED)
        licence_before = self.fresh_licence()

        out = self.resolve(
            intent,
            "--apply",
            "--outcome",
            "applied",
            "--by",
            self.superadmin.email,
            "--note",
            "Rolled forward in Stripe and the admin",
        )

        intent.refresh_from_db()
        self.assertEqual(intent.status, S.COMPLETE)
        self.assertEqual(intent.resolved_by, self.superadmin)
        self.assertIsNotNone(intent.resolved_at)
        self.assertEqual(
            intent.resolution_note, "Rolled forward in Stripe and the admin"
        )
        self.assertIn("closed as COMPLETE", out)
        # The guard is free: another change can be recorded.
        self.make_intent(S.PENDING)
        # It touched neither Stripe nor the licence.
        self.assertEqual(self.stripe.calls, [])
        # Every column of the licence, not a chosen few (1a R2): a write to
        # any field (is_active, say) must fail this.
        self.assertEqual(licence_row(self.fresh_licence()), licence_row(licence_before))

    def test_not_applied_closes_as_failed(self):
        intent = self.make_intent(S.PENDING)
        self.resolve(
            intent,
            "--apply",
            "--outcome",
            "not-applied",
            "--by",
            self.superadmin.email,
            "--note",
            "Checked Stripe: never applied",
        )
        self.assertEqual(self.status_of(intent), S.FAILED)

    def test_a_closed_intent_cannot_be_resolved(self):
        intent = self.make_intent(S.COMPLETE)
        with self.assertRaisesRegex(CommandError, "already closed"):
            self.resolve(intent)
