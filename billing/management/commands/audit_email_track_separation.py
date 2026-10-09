"""
Read-only audit: finds accounts that sit on the wrong side of the
personal/business email split, or on both sides at once.

WHY THIS EXISTS
The product keeps a person's individual account and their school account
deliberately separate -- personal email + individual subscription on one
side, business email + school license on the other. Three routes let
accounts land in a state that split forbids, all now closed:

  1. "Business email" was defined as "not one of 22 named consumer
     providers", so googlemail.com, yahoo.co.uk, proton.me, gmx.net and
     every throwaway-mail provider classified as BUSINESS. Any of them
     could create a school admin or take a license seat.
     (users.utils.is_business_email)

  2. The email rule only ran on account creation or on an email change, so
     changing an existing teacher's user_type to SCHOOL_ADMIN -- or
     attaching a school to a personal-email teacher -- skipped it entirely.
     (CustomUserSerializer.validate)

  3. Nothing stopped a teacher who already held an active license seat from
     buying an individual plan. Access resolves license-first
     (resolve_billing_context), so those accounts are billed every month
     for credits they can never spend.
     (IndividualPlanChangeService._assert_not_on_the_license_track)

Closing the doors does NOT repair rows already written through them. This
command finds those rows so a human can decide what each one should be.

It NEVER writes. The repairs are business decisions: asking a school admin
to move to a business address, or refunding an individual subscription that
a license already covers, are not things to do behind anyone's back.

Usage:
    python manage.py audit_email_track_separation            # report
    python manage.py audit_email_track_separation --strict   # exit 1 if any
"""

from django.core.management.base import BaseCommand

from billing.models import SchoolCreditAllocation, UserSubscription
from users.models import CustomUser, UserTypes
from users.utils import (
    email_domain,
    is_business_email,
    is_disposable_email,
    is_exempt_email_domain,
    is_personal_email,
)


class Command(BaseCommand):
    help = (
        "Audit (read-only) for accounts on the wrong side of the "
        "personal/business email split, or on both tracks at once."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--strict",
            action="store_true",
            help="Exit with status 1 if any finding is reported (for CI/cron).",
        )

    def handle(self, *args, **options):
        findings = 0

        findings += self._audit_school_admins_on_personal_emails()
        findings += self._audit_licensed_teachers_on_personal_emails()
        findings += self._audit_individual_teachers_on_business_emails()
        findings += self._audit_accounts_on_both_tracks()

        self.stdout.write("")
        if findings:
            self.stdout.write(
                self.style.WARNING(f"{findings} finding(s) need a human decision.")
            )
            if options["strict"]:
                raise SystemExit(1)
        else:
            self.stdout.write(self.style.SUCCESS("No findings. Nothing to repair."))

    def _describe(self, email: str) -> str:
        """Why this address fails, in the words a human needs to act on."""

        domain = email_domain(email)
        if not domain:
            return "not a usable email address"
        if is_disposable_email(email):
            return f"{domain} is a throwaway-mail provider"
        if is_personal_email(email):
            return f"{domain} is a consumer mailbox provider"
        return f"{domain} is not a consumer provider"

    # -- 1. School admins who would now be rejected ------------------------

    def _audit_school_admins_on_personal_emails(self):
        self.stdout.write(
            self.style.MIGRATE_HEADING("School admins on a non-business email")
        )

        found = 0
        admins = CustomUser.objects.filter(
            user_type=UserTypes.SCHOOL_ADMIN
        ).select_related("school")

        for admin in admins:
            if is_exempt_email_domain(admin.email) or is_business_email(admin.email):
                continue

            found += 1
            school = admin.school.name if admin.school else "(no school)"
            self.stdout.write(
                self.style.ERROR(
                    f"  {admin.email} administers {school!r} -- "
                    f"{self._describe(admin.email)}"
                )
            )
            self.stdout.write(
                "    -> ask them to move to their school's address; the "
                "account cannot be recreated on this one"
            )

        if not found:
            self.stdout.write("  none")
        return found

    # -- 2. Teachers holding a license seat on a personal email ------------

    def _audit_licensed_teachers_on_personal_emails(self):
        self.stdout.write(
            self.style.MIGRATE_HEADING("License seats held on a non-business email")
        )

        found = 0
        allocations = SchoolCreditAllocation.objects.filter(
            is_active=True,
            is_admin_allocation=False,
            license_subscription__is_active=True,
        ).select_related("user", "license_subscription__school")

        for allocation in allocations:
            email = allocation.user.email
            if is_exempt_email_domain(email) or is_business_email(email):
                continue

            found += 1
            school = allocation.license_subscription.school.name
            self.stdout.write(
                self.style.ERROR(
                    f"  {email} holds a seat on {school!r}'s license -- "
                    f"{self._describe(email)}"
                )
            )
            self.stdout.write(
                "    -> this seat could not be granted today; the enrollment "
                "path now refuses this address"
            )

        if not found:
            self.stdout.write("  none")
        return found

    # -- 3. Individual-track teachers on a business email ------------------

    def _audit_individual_teachers_on_business_emails(self):
        self.stdout.write(
            self.style.MIGRATE_HEADING("Individual teachers on a non-personal email")
        )

        found = 0
        # A teacher with no school and no license seat is on the individual
        # track, and the individual track requires a personal mailbox.
        # License-invited teachers legitimately hold business addresses, so
        # they are excluded by the school/allocation filters rather than by
        # the email test.
        teachers = CustomUser.objects.filter(
            user_type=UserTypes.TEACHER, school__isnull=True
        ).exclude(
            school_credit_allocations__is_active=True,
        )

        for teacher in teachers:
            email = teacher.email
            if (
                not email
                or email.endswith("@student.local")
                or is_exempt_email_domain(email)
                or is_personal_email(email)
            ):
                continue

            found += 1
            self.stdout.write(
                self.style.ERROR(
                    f"  {email} is an individual-track teacher -- "
                    f"{self._describe(email)}"
                )
            )

        if not found:
            self.stdout.write("  none")
        return found

    # -- 4. Accounts billed on BOTH tracks ---------------------------------

    def _audit_accounts_on_both_tracks(self):
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                "Accounts on both tracks (paying twice, using one)"
            )
        )

        found = 0
        allocations = SchoolCreditAllocation.objects.filter(
            is_active=True,
            license_subscription__is_active=True,
        ).select_related("user", "license_subscription__school")

        for allocation in allocations:
            individual = (
                UserSubscription.objects.filter(user=allocation.user, is_active=True)
                .select_related("plan")
                .first()
            )
            if not individual:
                continue

            found += 1
            school = allocation.license_subscription.school.name
            kind = "admin" if allocation.is_admin_allocation else "teacher"
            self.stdout.write(
                self.style.ERROR(
                    f"  {allocation.user.email} holds a license {kind} seat on "
                    f"{school!r} AND an active individual "
                    f"{individual.plan.display_name} subscription"
                )
            )
            self.stdout.write(
                "    -> access resolves license-first, so the individual "
                "subscription is never used. Check whether it has been "
                "charged, and whether a refund is owed."
                + ("" if individual.is_trial else " This one is PAID.")
            )

        if not found:
            self.stdout.write("  none")
        return found
