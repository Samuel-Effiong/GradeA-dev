"""
billing/tests/test_audit_school_admins.py
=============================================
Covers the audit_school_admins management command.

The command exists because closing the two doors that let a superadmin
become a school's admin does nothing for rows already written through
them. Its whole job is finding those rows, so the tests build them the
only way that's still possible - writing the model directly, bypassing
the serializer and service guards, exactly as the pre-fix code paths did.
"""

from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from billing.models import (
    LicenseBillingMethod,
    LicenseSubscription,
    PlanCategory,
    PlanTier,
    PlanType,
    SubscriptionPlan,
)
from classrooms.models import School
from users.models import CustomUser, UserTypes


class AuditSchoolAdminsCommandTest(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Audit School")
        self.plan = SubscriptionPlan.objects.create(
            name=PlanType.PRO,
            display_name="Audit Plan",
            category=PlanCategory.LICENSE,
            tier=PlanTier.PRO,
            monthly_credits=20000,
        )
        self.school_admin = CustomUser.objects.create_user(
            email="admin@audit.edu",
            password="password123",  # pragma: allowlist secret
            first_name="Ada",
            last_name="Lovelace",
            user_type=UserTypes.SCHOOL_ADMIN,
            school=self.school,
            is_active=True,
        )
        self.superadmin = CustomUser.objects.create_superuser(
            email="super@audit.example",
            password="password123",  # pragma: allowlist secret
            first_name="Super",
            last_name="Admin",
        )
        self.superadmin.user_type = UserTypes.SUPER_ADMIN
        self.superadmin.save()

    def _run(self, *args):
        out = StringIO()
        call_command("audit_school_admins", *args, stdout=out, stderr=out)
        return out.getvalue()

    def test_clean_database_reports_nothing(self):
        LicenseSubscription.objects.create(
            school=self.school,
            admin_user=self.school_admin,
            plan=self.plan,
            contract_months=12,
            max_seats=5,
            billing_cycle_start="2026-01-01T00:00:00Z",
            billing_cycle_end="2027-01-01T00:00:00Z",
            billing_method=LicenseBillingMethod.OFFLINE,
        )

        output = self._run()

        self.assertIn("No findings", output)

    def test_detects_a_legacy_license_owned_by_a_superadmin(self):
        """Written directly, the way the pre-fix serializer would have."""
        LicenseSubscription.objects.create(
            school=self.school,
            admin_user=self.superadmin,
            plan=self.plan,
            contract_months=12,
            max_seats=5,
            billing_cycle_start="2026-01-01T00:00:00Z",
            billing_cycle_end="2027-01-01T00:00:00Z",
            billing_method=LicenseBillingMethod.OFFLINE,
        )

        output = self._run()

        self.assertIn("super@audit.example", output)
        self.assertIn("Audit School", output)
        # It should also name the admin it would hand the license back to.
        self.assertIn("admin@audit.edu", output)
        self.assertNotIn("No findings", output)

    def test_detects_a_superuser_account_attached_to_a_school(self):
        self.superadmin.school = self.school
        self.superadmin.user_type = UserTypes.SCHOOL_ADMIN
        self.superadmin.save(update_fields=["school", "user_type"])

        output = self._run()

        self.assertIn("super@audit.example", output)
        self.assertIn("shows as this school's admin", output)

    def test_detects_a_school_with_no_admin(self):
        School.objects.create(name="Orphan School")

        output = self._run()

        self.assertIn("Orphan School", output)

    def test_strict_mode_exits_nonzero_on_findings(self):
        School.objects.create(name="Orphan School")

        with self.assertRaises(SystemExit):
            self._run("--strict")

    def test_strict_mode_exits_zero_when_clean(self):
        # Should not raise.
        self._run("--strict")

    def test_audit_never_writes(self):
        """It reports; a human repairs."""
        license_sub = LicenseSubscription.objects.create(
            school=self.school,
            admin_user=self.superadmin,
            plan=self.plan,
            contract_months=12,
            max_seats=5,
            billing_cycle_start="2026-01-01T00:00:00Z",
            billing_cycle_end="2027-01-01T00:00:00Z",
            billing_method=LicenseBillingMethod.OFFLINE,
        )
        self.superadmin.school = self.school
        self.superadmin.save(update_fields=["school"])

        self._run()

        license_sub.refresh_from_db()
        self.superadmin.refresh_from_db()
        self.assertEqual(license_sub.admin_user, self.superadmin)
        self.assertEqual(self.superadmin.school, self.school)
