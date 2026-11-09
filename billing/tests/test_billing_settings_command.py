"""B1b-4 (NOTES §4, 08b §1.3 item 5): `billing_settings`, the operator's way
to read and change the BillingSettings row (it stays out of Django admin).
Each change is one STATE_CHANGE audit row naming the super admin given by
--by, with the field, old and new value."""

from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from audit.enums import AuditAction
from audit.models import AuditEvent
from billing.models import BillingSettings
from users.models import CustomUser, UserTypes


def super_admin(email="est.ops@example.com"):
    admin = CustomUser.objects.create_superuser(
        email=email,
        password="pw-12345678",  # pragma: allowlist secret  # nosec B106
        first_name="Ops",
        last_name="Admin",
    )
    admin.user_type = UserTypes.SUPER_ADMIN
    admin.save()
    return admin


def run(*args):
    out = StringIO()
    call_command("billing_settings", *args, stdout=out)
    return out.getvalue()


class BillingSettingsCommandTests(TestCase):
    def setUp(self):
        self.admin = super_admin()

    def test_show_prints_every_field(self):
        text = run("--show")
        self.assertIn("cold_start_cost_factor=3.0", text)
        self.assertIn("unstarted_backstop_hours=36", text)
        self.assertIn("hold_sweep_margin_minutes=15", text)

    def test_set_changes_one_field_and_writes_one_audit_row(self):
        before = set(AuditEvent.objects.values_list("pk", flat=True))
        run("--set", "cold_start_cost_factor=2.4", "--by", self.admin.email)
        self.assertEqual(BillingSettings.load().cold_start_cost_factor, 2.4)
        rows = AuditEvent.objects.exclude(pk__in=before)
        self.assertEqual(rows.count(), 1)
        row = rows.get()
        self.assertEqual(row.action, AuditAction.STATE_CHANGE)
        self.assertEqual(row.actor_id, self.admin.pk)
        self.assertEqual(row.target_type, "BillingSettings")
        self.assertEqual(row.before, {"cold_start_cost_factor": 3.0})
        self.assertEqual(row.after, {"cold_start_cost_factor": 2.4})

    def test_two_sets_are_two_rows(self):
        before = set(AuditEvent.objects.values_list("pk", flat=True))
        run(
            "--set",
            "cold_start_cost_factor=2.4",
            "--set",
            "unstarted_backstop_hours=40",
            "--by",
            self.admin.email,
        )
        self.assertEqual(AuditEvent.objects.exclude(pk__in=before).count(), 2)
        self.assertEqual(BillingSettings.load().unstarted_backstop_hours, 40)

    def test_set_needs_by(self):
        with self.assertRaises(CommandError):
            run("--set", "cold_start_cost_factor=2.4")
        self.assertEqual(BillingSettings.load().cold_start_cost_factor, 3.0)

    def test_by_must_be_an_active_super_admin(self):
        teacher = CustomUser.objects.create_user(
            email="est.ops.teacher@example.com",
            password="pw-12345678",  # pragma: allowlist secret  # nosec B106
            user_type=UserTypes.TEACHER,
            is_active=True,
        )
        for by in (teacher.email, "nobody@example.com"):
            with self.subTest(by=by), self.assertRaises(CommandError):
                run("--set", "cold_start_cost_factor=2.4", "--by", by)
        self.assertEqual(BillingSettings.load().cold_start_cost_factor, 3.0)

    def test_an_unknown_field_is_refused(self):
        with self.assertRaises(CommandError):
            run("--set", "pk=7", "--by", self.admin.email)
        with self.assertRaises(CommandError):
            run("--set", "nonsense=1", "--by", self.admin.email)

    def test_out_of_bounds_and_wrong_type_values_are_refused(self):
        for value in (
            "cold_start_cost_factor=0",
            "cold_start_cost_factor=-1",
            "cold_start_cost_factor=nan",
            "cold_start_cost_factor=inf",
            "cold_start_cost_factor=abc",
            "unstarted_backstop_hours=-1",
            "unstarted_backstop_hours=40000",
            "unstarted_backstop_hours=1.5",
            "hold_sweep_margin_minutes=-5",
        ):
            with self.subTest(value=value), self.assertRaises(CommandError):
                run("--set", value, "--by", self.admin.email)
        row = BillingSettings.load()
        self.assertEqual(
            (
                row.cold_start_cost_factor,
                row.unstarted_backstop_hours,
                row.hold_sweep_margin_minutes,
            ),
            (3.0, 36, 15),
        )
        self.assertFalse(
            AuditEvent.objects.filter(target_type="BillingSettings").exists()
        )

    def test_a_refused_set_changes_nothing_even_after_a_good_one(self):
        with self.assertRaises(CommandError):
            run(
                "--set",
                "cold_start_cost_factor=2.4",
                "--set",
                "unstarted_backstop_hours=-1",
                "--by",
                self.admin.email,
            )
        self.assertEqual(BillingSettings.load().cold_start_cost_factor, 3.0)
