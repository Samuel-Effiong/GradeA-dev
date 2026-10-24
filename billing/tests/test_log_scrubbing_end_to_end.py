"""
H-89: the address scrubber, switched on, through real licence code.

The scrubber is off under the test runner (AutoGrader/log_scrubbing.py says
why), so the suite as a whole never runs with it on. These tests do: they
drive the path a school admin's add-teachers request takes, and one that
fails with a traceback, with a real handler and formatter attached, and
check that nothing raises, the output is scrubbed and the ids survive.
"""

import io
import logging
from unittest.mock import patch

from django.test import TestCase, override_settings

from billing.license_service import LicenseSubscriptionService
from billing.models import (
    LicenseBillingMethod,
    PlanCategory,
    PlanTier,
    PlanType,
    SchoolCreditAllocation,
    SubscriptionPlan,
)
from classrooms.models import School
from users.models import CustomUser, UserTypes

TEACHER = "new.teacher@h89school.edu"


@override_settings(LOG_SCRUB_ADDRESSES=True)
class ScrubberOnEndToEndTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="H89 School")
        self.plan = SubscriptionPlan.objects.create(
            name=PlanType.PRO,
            display_name="H89 Licence Plan",
            category=PlanCategory.LICENSE,
            tier=PlanTier.PRO,
            monthly_credits=20000,
        )
        self.admin = CustomUser.objects.create_user(
            email="admin@h89school.edu",
            password="password123",  # pragma: allowlist secret
            first_name="School",
            last_name="Admin",
            user_type=UserTypes.SCHOOL_ADMIN,
            school=self.school,
            is_active=True,
        )
        mail = patch("billing.license_service.send_email_task")
        mail.start()
        self.addCleanup(mail.stop)
        self.licence = LicenseSubscriptionService.create_license_subscription(
            school=self.school,
            plan=self.plan,
            teacher_emails=[],
            max_seats=5,
            billing_method=LicenseBillingMethod.OFFLINE,
        )
        # A real handler and formatter on the service's logger, as a web
        # process or a worker would have.
        self.stream = io.StringIO()
        handler = logging.StreamHandler(self.stream)
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
        logger = logging.getLogger("billing.license_service")
        previous_level = logger.level
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        self.addCleanup(logger.removeHandler, handler)
        self.addCleanup(logger.setLevel, previous_level)

    def add_teacher(self):
        with self.captureOnCommitCallbacks(execute=True):
            return LicenseSubscriptionService._invite_and_enroll_one_teacher(
                self.licence, self.school, self.admin, TEACHER
            )

    def test_adding_a_teacher_logs_as_before_with_no_address(self):
        result = self.add_teacher()

        self.assertTrue(result["successful"], result)
        teacher = CustomUser.objects.get(email=TEACHER)
        self.assertTrue(
            SchoolCreditAllocation.objects.filter(
                license_subscription=self.licence, user=teacher, is_active=True
            ).exists()
        )
        output = self.stream.getvalue()
        self.assertIn(
            f"Enrolled teacher {teacher.id} in license {self.licence.id}", output
        )
        self.assertIn("Queued teacher invitation email", output)
        self.assertNotIn("@", output)

    def test_an_unexpected_failure_keeps_its_traceback_and_loses_the_address(self):
        """The known limit H-80 recorded: the traceback prints the
        exception's own text."""
        with patch.object(
            LicenseSubscriptionService,
            "_enroll_teacher_internal",
            side_effect=RuntimeError(f"could not enrol {TEACHER}: connection reset"),
        ):
            result = self.add_teacher()

        self.assertFalse(result["successful"])
        output = self.stream.getvalue()
        self.assertIn("Unexpected error enrolling a teacher", output)
        self.assertIn(str(self.licence.id), output)
        self.assertIn("Traceback (most recent call last)", output)
        self.assertIn("RuntimeError: could not enrol [email]: connection reset", output)
        self.assertNotIn(TEACHER, output)
        self.assertNotIn("@", output)

    def test_control_with_the_scrubber_off_the_traceback_has_the_address(self):
        """What these tests would see without it: the reason for H-89."""
        with override_settings(LOG_SCRUB_ADDRESSES=False):
            with patch.object(
                LicenseSubscriptionService,
                "_enroll_teacher_internal",
                side_effect=RuntimeError(f"could not enrol {TEACHER}"),
            ):
                self.add_teacher()

        self.assertIn(
            f"RuntimeError: could not enrol {TEACHER}", self.stream.getvalue()
        )
