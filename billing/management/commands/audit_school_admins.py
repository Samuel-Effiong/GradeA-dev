"""
Read-only audit: finds accounts and licenses where the "school admin" is
somebody who shouldn't hold that role — most importantly a superadmin.

WHY THIS EXISTS
QA reported superadmins showing up as the admin of schools. Two separate
routes allowed it, both now closed:

  1. LicenseSubscription.admin_user accepted any non-student whose `school`
     was unset — which is exactly a superadmin — so a license could name
     platform staff as the school's billing admin. That diverted the
     school's admin credit allocation into the superadmin's wallet and
     locked the school's real admin out of the license.
     (LicenseSubscriptionService.validate_admin_user)

  2. CustomUser allowed a superuser account to be given a school and a
     user_type of SCHOOL_ADMIN, after which it appeared as that school's
     admin on every school screen while still holding is_superuser.
     (CustomUserSerializer.validate)

Closing the doors does NOT repair rows already written through them. This
command finds those rows so a human can decide what each one should be.

It NEVER writes. Reassignment is deliberately manual: picking the right
admin for a school is a business decision, and for a license it also moves
a credit allocation, which shouldn't happen behind anyone's back.

Usage:
    python manage.py audit_school_admins            # human-readable report
    python manage.py audit_school_admins --strict   # exit 1 if anything found
"""

from django.core.management.base import BaseCommand

from billing.license_service import LicenseSubscriptionService
from billing.models import LicenseSubscription
from classrooms.models import School
from users.models import CustomUser, UserTypes


class Command(BaseCommand):
    help = (
        "Audit (read-only) for superadmins or other ineligible users holding "
        "school-admin roles, on user accounts and on license subscriptions."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--strict",
            action="store_true",
            help="Exit with status 1 if any finding is reported (for CI/cron).",
        )

    def handle(self, *args, **options):
        findings = 0

        findings += self._audit_platform_staff_in_tenants()
        findings += self._audit_license_admin_users()
        findings += self._audit_schools_without_admins()

        self.stdout.write("")
        if findings:
            self.stdout.write(
                self.style.WARNING(f"{findings} finding(s) need a human decision.")
            )
            if options["strict"]:
                raise SystemExit(1)
        else:
            self.stdout.write(self.style.SUCCESS("No findings. Nothing to repair."))

    # -- 1. Accounts that are platform staff AND tenant members ------------

    def _audit_platform_staff_in_tenants(self):
        self.stdout.write(
            self.style.MIGRATE_HEADING("Superadmins attached to a school")
        )

        # Either marker is disqualifying on its own: an account carrying
        # is_superuser with a tenant user_type is the more dangerous shape,
        # because every school screen selects on user_type alone.
        suspects = CustomUser.objects.filter(school__isnull=False).filter(
            is_superuser=True
        ) | CustomUser.objects.filter(
            school__isnull=False, user_type=UserTypes.SUPER_ADMIN
        )
        suspects = suspects.distinct().select_related("school")

        if not suspects.exists():
            self.stdout.write("  none")
            return 0

        for user in suspects:
            self.stdout.write(
                self.style.ERROR(
                    f"  {user.email} (user_type={user.user_type}, "
                    f"is_superuser={user.is_superuser}) is attached to "
                    f"school {user.school.name!r}"
                )
            )
            if user.user_type == UserTypes.SCHOOL_ADMIN:
                self.stdout.write(
                    "    -> shows as this school's admin on school screens, "
                    "and cannot use superadmin endpoints while it does"
                )
        return suspects.count()

    # -- 2. Licenses whose admin_user would now be rejected ----------------

    def _audit_license_admin_users(self):
        self.stdout.write(
            self.style.MIGRATE_HEADING("Licenses with an ineligible admin_user")
        )

        found = 0
        licenses = LicenseSubscription.objects.select_related(
            "school", "admin_user"
        ).order_by("school__name")

        for license_sub in licenses:
            # Re-run the live guard rather than re-implementing it, so this
            # audit can never drift from what the code now enforces.
            try:
                LicenseSubscriptionService.validate_admin_user(
                    license_sub.admin_user, license_sub.school
                )
                continue
            except ValueError as exc:
                # Bind the message here: Python unbinds `exc` when the
                # except block ends.
                reason = str(exc)

            found += 1
            state = "active" if license_sub.is_active else "inactive"
            self.stdout.write(
                self.style.ERROR(
                    f"  {license_sub.school.name!r} ({state} license "
                    f"{license_sub.id}): admin_user={license_sub.admin_user.email} "
                    f"-- {reason}"
                )
            )

            # What it should probably be, and what has to move with it.
            try:
                suggested = LicenseSubscriptionService.resolve_admin_user(
                    license_sub.school
                )
                self.stdout.write(f"    -> school's own admin is {suggested.email}")
            except ValueError as resolve_exc:
                self.stdout.write(f"    -> no replacement available: {resolve_exc}")

            diverted = license_sub.allocations.filter(
                is_admin_allocation=True, user=license_sub.admin_user
            ).first()
            if diverted:
                self.stdout.write(
                    f"    -> admin credit allocation {diverted.id} "
                    f"({diverted.monthly_allocation} credits/mo) currently "
                    f"belongs to {license_sub.admin_user.email}"
                )

        if not found:
            self.stdout.write("  none")
        return found

    # -- 3. Schools that have nobody eligible ------------------------------

    def _audit_schools_without_admins(self):
        self.stdout.write(
            self.style.MIGRATE_HEADING("Active schools with no school admin")
        )

        found = 0
        for school in School.objects.filter(is_active=True).order_by("name"):
            if CustomUser.objects.filter(
                school=school, user_type=UserTypes.SCHOOL_ADMIN
            ).exists():
                continue

            found += 1
            has_license = LicenseSubscription.objects.filter(
                school=school, is_active=True
            ).exists()
            note = " (HAS AN ACTIVE LICENSE)" if has_license else ""
            self.stdout.write(self.style.WARNING(f"  {school.name!r}{note}"))
            self.stdout.write(
                "    -> license creation for this school will now fail until "
                "it has an admin"
            )

        if not found:
            self.stdout.write("  none")
        return found
