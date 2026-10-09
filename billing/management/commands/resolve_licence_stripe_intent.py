"""
Close a licence Stripe-change intent that a human has reconciled (H-28).

WHEN YOU NEED THIS
-------------------
A licence billing change that cannot finish on its own is ESCALATED (or,
if its worker died, left PENDING / STRIPE_APPLIED until the stale-intent
check escalates it), and every super admin is emailed. While it is open,
the per-licence guard refuses every further Stripe change to that licence,
so without a way to close it one escalation would lock a school's billing
for good.

WHAT IT DOES, AND DOES NOT
---------------------------
It ONLY closes the intent and frees the guard. It never calls Stripe and
never changes the licence: reconcile both BEFORE running it, in Stripe's
dashboard and the Django admin or shell. A command that "fixed" Stripe
itself would be automated money movement, which the design rules out
(the SM's ruling, DESIGN_PROPOSAL.md §9h-bis). It is a command, not an
admin action, so no new writable billing surface is added.

USAGE
------
    # Dry run (the default): what the intent records, the licence as the
    # application sees it now, and what the disagreement means.
    manage.py resolve_licence_stripe_intent <intent-id>

    # Close it, once Stripe and the licence agree:
    manage.py resolve_licence_stripe_intent <intent-id> --apply \\
        --outcome applied --by admin@example.com --note "Rolled forward: ..."

--outcome applied      the change now stands on both sides -> COMPLETE
--outcome not-applied  neither side has the change           -> FAILED
"""

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from audit.context import command_actor
from billing.license_stripe_mutation import audit_intent_status
from billing.models import LicenseStripeMutationIntent, LicenseStripeMutationStatus
from users.models import CustomUser, UserTypes

COMMAND = Path(__file__).stem

IN_FLIGHT = (
    LicenseStripeMutationStatus.PENDING,
    LicenseStripeMutationStatus.STRIPE_APPLIED,
    LicenseStripeMutationStatus.ESCALATED,
)

OUTCOMES = {
    "applied": LicenseStripeMutationStatus.COMPLETE,
    "not-applied": LicenseStripeMutationStatus.FAILED,
}

MEANING = {
    LicenseStripeMutationStatus.PENDING: (
        "The outcome at Stripe is UNKNOWN: the change may or may not have "
        "been applied. Check the Stripe subscription first."
    ),
    LicenseStripeMutationStatus.STRIPE_APPLIED: (
        "Stripe APPLIED the change; the application never recorded it."
    ),
    LicenseStripeMutationStatus.ESCALATED: (
        "The code gave up; the failure reason below says where. Money may "
        "have moved: roll forward, do not refund without a decision."
    ),
}


class Command(BaseCommand):
    help = (
        "Show (default) or close (--apply) a licence Stripe-change intent "
        "after a human has reconciled Stripe and the licence. Never calls "
        "Stripe and never changes the licence."
    )

    def add_arguments(self, parser):
        parser.add_argument("intent_id")
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--outcome", choices=sorted(OUTCOMES))
        parser.add_argument("--by", help="Email of the super admin who reconciled it.")
        parser.add_argument("--note", help="What was done, and where.")

    def handle(self, *args, **options):
        try:
            intent = LicenseStripeMutationIntent.objects.select_related(
                "license_subscription__school", "license_subscription__plan"
            ).get(pk=options["intent_id"])
        except (LicenseStripeMutationIntent.DoesNotExist, ValueError) as exc:
            raise CommandError(f"No intent {options['intent_id']!r}.") from exc

        self._describe(intent)

        if intent.status not in IN_FLIGHT:
            raise CommandError(
                f"This intent is {intent.status}, already closed; nothing to resolve."
            )
        if not options["apply"]:
            self.stdout.write(
                "\nDry run: nothing changed. When Stripe and the licence agree, "
                "re-run with --apply --outcome {applied|not-applied} "
                "--by <super admin email> --note <what was done>."
            )
            return

        outcome = options["outcome"]
        note = (options["note"] or "").strip()
        if not outcome:
            raise CommandError("--apply needs --outcome applied or not-applied.")
        if not note:
            raise CommandError("--apply needs --note: what was done, and where.")
        resolver = self._super_admin(options["by"])

        now = timezone.now()
        # The close and its audit event commit together. The event names the
        # resolver and this command (command_actor); the note stays on the
        # intent and never goes into the audit trail.
        with transaction.atomic(), command_actor(resolver, command=COMMAND):
            closed = LicenseStripeMutationIntent.objects.filter(
                pk=intent.pk, status=intent.status
            ).update(
                status=OUTCOMES[outcome],
                resolved_at=now,
                resolved_by=resolver,
                resolution_note=note,
                updated_at=now,
            )
            if closed:
                audit_intent_status(intent, intent.status, OUTCOMES[outcome])
        if not closed:
            raise CommandError(
                "The intent changed while this ran; run it again to see it now."
            )
        self.stdout.write(
            self.style.SUCCESS(
                f"Intent {intent.id} closed as {OUTCOMES[outcome]} by "
                f"{resolver.email}. The licence can be changed again."
            )
        )

    def _describe(self, intent):
        licence = intent.license_subscription
        lines = [
            f"Intent:               {intent.id}",
            f"Operation:            {intent.operation}",
            f"Status:               {intent.status}",
            f"Created:              {intent.created_at.isoformat()}",
            f"Escalated:            {intent.escalated_at.isoformat() if intent.escalated_at else '-'}",
            f"Stripe subscription:  {intent.stripe_subscription_id}",
            f"Requested change:     {json.dumps(intent.requested_change, sort_keys=True)}",
            f"Stripe result:        {json.dumps(intent.stripe_result, sort_keys=True)}",
            f"Failure reason:       {intent.failure_reason or '-'}",
            "",
            f"Licence (as the application sees it now): {licence.id}, {licence.school.name}",
            f"  billing method:       {licence.billing_method}",
            f"  Stripe subscription:  {licence.stripe_subscription_id}",
            f"  plan / custom price:  {licence.plan.name} / {licence.custom_price_cents}",
            f"  seats:                {licence.max_seats}",
            f"  active / auto-renew:  {licence.is_active} / {licence.auto_renew}",
        ]
        meaning = MEANING.get(intent.status)
        if meaning:
            lines += ["", meaning]
        self.stdout.write("\n".join(lines))

    @staticmethod
    def _super_admin(email):
        if not email:
            raise CommandError("--apply needs --by: the super admin who reconciled it.")
        user = CustomUser.objects.filter(
            email__iexact=email,
            user_type=UserTypes.SUPER_ADMIN,
            is_superuser=True,
            is_active=True,
        ).first()
        if user is None:
            raise CommandError(f"{email!r} is not an active super admin.")
        return user
