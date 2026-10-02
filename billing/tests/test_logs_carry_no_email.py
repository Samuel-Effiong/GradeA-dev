"""
H-80 / H-86: billing/license_service.py and users/signals.py log ids, never
a user's email address.

H-80: about 30 logger calls formatted teacher, admin and super-admin
addresses (enrolment, carry-forward, invitations, wallet creation, removal,
overage and renewal notices, signals), one logged whole failed_results
dicts (address + refusal text), and several logged an exception's text,
which for the enrolment refusals carries the address. They now log user,
licence, school and request ids, and an exception's class.

H-86: refusals raised inside _enroll_teacher_internal (the seat limit, a
teacher of another school) reached the operator only as "Skipped enrolling
... ValueError". Each now logs its own ids-only reason line first.

The guard reads both modules' source: no logger call may pass an `.email`,
a name holding an address, failed_results, or an exception's text. The
behaviour tests drive the paths 1a's probes found leaking.
"""

import ast
import os
from datetime import timedelta
from unittest.mock import patch

from django.conf import settings
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from billing.license_service import LicenseSubscriptionService
from billing.models import (
    LicenseBillingMethod,
    PlanCategory,
    PlanTier,
    PlanType,
    SubscriptionPlan,
    UserSubscription,
)
from billing.services import SubscriptionService
from classrooms.models import School
from users.models import CustomUser, UserTypes

GUARDED = ["billing/license_service.py", "users/signals.py"]
GUARDED_LOGGERS = ("billing.license_service", "users.signals")
EXCEPTION_NAMES = {"e", "exc"}


def logger_calls(path):
    with open(os.path.join(settings.BASE_DIR, path), encoding="utf-8") as fh:
        source = fh.read()
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "logger"
        ):
            yield node


def leaks(call):
    """What in this logger call may carry an address: the message must be a
    plain literal (no %, +, .format or f-string building it), and no
    argument, the message included, may hold an address."""
    found = []
    if call.args and not _is_plain_literal(call.args[0]):
        found.append("a message that is not a literal")
    for arg in call.args:
        if isinstance(arg, ast.Name) and arg.id in EXCEPTION_NAMES:
            found.append(f"the exception's text ({arg.id})")
        for node in ast.walk(arg):
            if isinstance(node, ast.Attribute) and node.attr == "email":
                found.append(ast.unparse(node))
            elif isinstance(node, ast.Name) and (
                "email" in node.id.lower() or node.id == "failed_results"
            ):
                if not _is_len_call(arg, node):
                    found.append(node.id)
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "str"
                and node.args
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id in EXCEPTION_NAMES
            ):
                found.append(f"str({node.args[0].id})")
    return found


def _is_plain_literal(message):
    """A string constant, or string constants joined by `+`."""
    if isinstance(message, ast.Constant):
        return isinstance(message.value, str)
    if isinstance(message, ast.BinOp) and isinstance(message.op, ast.Add):
        return _is_plain_literal(message.left) and _is_plain_literal(message.right)
    return False


def _is_len_call(arg, name):
    return (
        isinstance(arg, ast.Call)
        and isinstance(arg.func, ast.Name)
        and arg.func.id == "len"
        and arg.args
        and arg.args[0] is name
    )


def assertGuardedLinesHaveNoAddress(test, logs):
    """The guarded modules' lines only: other modules are out of scope."""
    lines = [
        record.getMessage() for record in logs.records if record.name in GUARDED_LOGGERS
    ]
    test.assertTrue(lines, "nothing logged by the guarded modules")
    test.assertEqual([line for line in lines if "@" in line], [])


class NoEmailInLogCallsTest(SimpleTestCase):
    def test_no_logger_call_formats_an_address(self):
        problems = []
        for path in GUARDED:
            for call in logger_calls(path):
                for leak in leaks(call):
                    problems.append(f"{path}:{call.lineno}: {leak}")
        self.assertEqual(problems, [], "\n".join(problems))

    def test_the_guard_sees_each_kind_of_leak(self):
        """Not vacuous: each shape the guard rejects, in a sample call."""
        samples = {
            'logger.info("x %s", teacher.email)': "teacher.email",
            'logger.info("x %s", allocation.user.email)': "allocation.user.email",
            'logger.info("x %s", teacher_email)': "teacher_email",
            'logger.error("x %s", failed_results)': "failed_results",
            'logger.error("x %s", exc)': "the exception's text (exc)",
            'logger.error("x %s", str(e))': "str(e)",
            "logger.warning(error_msg)": "a message that is not a literal",
            # 1a's P2: an address formatted into the message itself.
            'logger.info("x %s" % teacher.email)': "teacher.email",
            'logger.info("x " + teacher.email)': "teacher.email",
            'logger.info("x {}".format(teacher.email))': "teacher.email",
            'logger.info(f"x {teacher.email}")': "teacher.email",
            'logger.info("x %s" % teacher.id)': "a message that is not a literal",
            'logger.info("x " + name)': "a message that is not a literal",
            'logger.info("x {}".format(teacher.id))': "a message that is not a literal",
            'logger.info(f"x {teacher.id}")': "a message that is not a literal",
            'logger.error("x %s" % exc)': "a message that is not a literal",
        }
        for code, expected in samples.items():
            with self.subTest(code=code):
                call = ast.parse(code, mode="eval").body
                self.assertIn(expected, leaks(call))
        for clean in (
            'logger.info("x %s", teacher.id)',
            'logger.error("x %d", len(failed_results))',
            'logger.error("x %s", type(exc).__name__)',
            'logger.info("x " + "y %s", teacher.id)',
            'logger.info("x " "y %s", teacher.id)',
        ):
            with self.subTest(clean=clean):
                self.assertEqual(leaks(ast.parse(clean, mode="eval").body), [])


class LicencePathsLogNoAddressTest(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="H80 School")
        self.plan = SubscriptionPlan.objects.create(
            name=PlanType.PRO,
            display_name="H80 Licence Plan",
            category=PlanCategory.LICENSE,
            tier=PlanTier.PRO,
            monthly_credits=20000,
        )
        self.individual_plan = SubscriptionPlan.objects.create(
            name=PlanType.STANDARD,
            display_name="H80 Individual Plan",
            category=PlanCategory.INDIVIDUAL,
            tier=PlanTier.STANDARD,
            monthly_credits=5000,
        )
        self.admin = CustomUser.objects.create_user(
            email="admin@h80school.edu",
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
        self.licence = self.new_licence(max_seats=5)

    def new_licence(self, max_seats):
        return LicenseSubscriptionService.create_license_subscription(
            school=self.school,
            plan=self.plan,
            teacher_emails=[],
            max_seats=max_seats,
            billing_method=LicenseBillingMethod.OFFLINE,
        )

    def teacher(self, email):
        return CustomUser.objects.create_user(
            email=email,
            password="password123",  # pragma: allowlist secret
            first_name="Some",
            last_name="Teacher",
            user_type=UserTypes.TEACHER,
            school=self.school,
            is_active=True,
        )

    def assertNoAddress(self, logs):
        assertGuardedLinesHaveNoAddress(self, logs)

    def test_carrying_teachers_to_a_new_licence_logs_no_address(self):
        """1a's R2: a carried-forward teacher now has an individual plan."""
        teacher = self.teacher("carried@h80school.edu")
        LicenseSubscriptionService._enroll_teacher_internal(self.licence, teacher)
        UserSubscription.objects.create(
            user=teacher,
            plan=self.individual_plan,
            is_active=True,
            billing_cycle_start=timezone.now(),
            billing_cycle_end=timezone.now() + timedelta(days=30),
        )

        with self.assertLogs(level="DEBUG") as logs:
            self.new_licence(max_seats=5)

        self.assertNoAddress(logs)

    def test_a_failed_licence_creation_summary_logs_no_address(self):
        """create_license_subscription logged the failed_results dicts."""
        with self.assertLogs(level="DEBUG") as logs:
            LicenseSubscriptionService.create_license_subscription(
                school=self.school,
                plan=self.plan,
                teacher_emails=["someone@gmail.com"],
                max_seats=5,
                billing_method=LicenseBillingMethod.OFFLINE,
            )

        self.assertTrue(any("FAILED" in line for line in logs.output))
        self.assertNoAddress(logs)

    def test_enrolling_and_inviting_logs_no_address(self):
        """The success path: invitation queued, wallet, enrolment, bucket."""
        with self.assertLogs(level="DEBUG") as logs:
            with self.captureOnCommitCallbacks(execute=True):
                result = LicenseSubscriptionService._invite_and_enroll_one_teacher(
                    self.licence, self.school, self.admin, "new@h80school.edu"
                )

        self.assertTrue(result["successful"], result)
        text = "\n".join(logs.output)
        self.assertIn("Queued teacher invitation email", text)
        self.assertIn("Enrolled teacher", text)
        self.assertNoAddress(logs)

    def test_a_seat_limit_refusal_logs_its_reason_with_ids(self):
        """H-86 (1a's R3): the operator learns why, by ids."""
        licence = self.new_licence(max_seats=1)
        first = self.teacher("first@h80school.edu")
        LicenseSubscriptionService._enroll_teacher_internal(licence, first)
        licence.refresh_from_db()
        second = self.teacher("second@h80school.edu")

        with self.assertLogs("billing.license_service", "WARNING") as logs:
            result = LicenseSubscriptionService._invite_and_enroll_one_teacher(
                licence, self.school, self.admin, second.email
            )

        self.assertFalse(result["successful"])
        [reason] = [line for line in logs.output if "seat limit" in line]
        self.assertIn(str(licence.id), reason)
        self.assertIn(str(second.id), reason)
        self.assertNoAddress(logs)

    def test_an_other_school_refusal_logs_its_reason_with_ids(self):
        """H-86: the enrolment's own school check says why, by ids."""
        other_school = School.objects.create(name="H80 Other School")
        outsider = self.teacher("outsider@h80other.edu")
        CustomUser.objects.filter(pk=outsider.pk).update(school=other_school)
        outsider.refresh_from_db()

        with self.assertLogs("billing.license_service", "WARNING") as logs:
            with self.assertRaises(ValueError):
                LicenseSubscriptionService._enroll_teacher_internal(
                    self.licence, outsider
                )

        [reason] = [line for line in logs.output if "belongs to school" in line]
        for part in (outsider.id, other_school.id, self.licence.id, self.school.id):
            self.assertIn(str(part), reason)
        self.assertNoAddress(logs)


class SignalsLogNoAddressTest(TestCase):
    def test_creating_a_user_logs_no_address(self):
        with self.assertLogs(level="DEBUG") as logs:
            CustomUser.objects.create_user(
                email="signals@h80school.edu",
                password="password123",  # pragma: allowlist secret
                user_type=UserTypes.TEACHER,
            )

        assertGuardedLinesHaveNoAddress(self, logs)


class FreeTrialRefusalsLogAReasonTest(TestCase):
    """1a's P1: users.signals logs only the refusal's class, so each refusal
    in activate_automatic_free_trial logs its own reason, by id."""

    def new_teacher(self):
        return CustomUser.objects.create_user(
            email="trial@h80school.edu",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )

    def test_a_missing_trial_plan_is_logged_as_an_error(self):
        """A configuration fault: without it every new teacher is silently
        denied a trial."""
        SubscriptionPlan.objects.filter(
            tier=PlanTier.TRIAL, category=PlanCategory.INDIVIDUAL
        ).delete()
        user = self.new_teacher()
        UserSubscription.objects.filter(user=user).delete()

        with self.assertLogs("billing.services", "ERROR") as logs:
            with self.assertRaises(ValueError):
                SubscriptionService.activate_automatic_free_trial(user)

        [reason] = [line for line in logs.output if "Free trial plan not found" in line]
        self.assertIn("ERROR", reason)
        self.assertIn(str(user.id), reason)
        self.assertEqual([line for line in logs.output if "@" in line], [])

    def test_a_used_trial_is_logged_as_a_warning(self):
        plan = SubscriptionPlan.objects.create(
            name=PlanType.STANDARD,
            display_name="H80 Trial Stand-in",
            category=PlanCategory.INDIVIDUAL,
            tier=PlanTier.STANDARD,
            monthly_credits=5000,
        )
        user = self.new_teacher()
        UserSubscription.objects.filter(user=user).delete()
        UserSubscription.objects.create(
            user=user,
            plan=plan,
            is_active=False,
            is_trial=True,
            billing_cycle_start=timezone.now() - timedelta(days=30),
            billing_cycle_end=timezone.now() - timedelta(days=16),
        )

        with self.assertLogs("billing.services", "WARNING") as logs:
            with self.assertRaises(ValueError):
                SubscriptionService.activate_automatic_free_trial(user)

        [reason] = [
            line for line in logs.output if "already used the free trial" in line
        ]
        self.assertIn("WARNING", reason)
        self.assertIn(str(user.id), reason)
        self.assertEqual([line for line in logs.output if "@" in line], [])
