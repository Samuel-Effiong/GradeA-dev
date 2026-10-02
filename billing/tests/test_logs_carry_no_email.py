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

H-91: every other production file is held to the narrower rule that Epic A's
check-no-pii-in-logs hook enforces (NoPiiInAnyLogCallTest below): no logging
or print call may pass `.email`, `.first_name`, `.last_name` or
`.get_full_name`. It is the same definition on purpose, so that a file
which passes here can leave the epic's baseline at the next merge-down.
Exception text and messages that are not plain literals are not part of
that rule; H-89's output filter covers them.
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

# H-91: the Epic A hook's definition (scripts/check_no_pii_in_logs.py there).
PII_ATTRS = {"email", "first_name", "last_name", "get_full_name"}
LOG_METHODS = {
    "debug",
    "info",
    "warning",
    "warn",
    "error",
    "exception",
    "critical",
    "log",
}
#: The apps H-91 has cleaned so far; every production file under them.
PII_GUARDED_DIRS = ("billing",)
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


def is_log_or_print_call(node):
    """A call to print(), or to a logging method on any receiver."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == "print"
    return isinstance(func, ast.Attribute) and func.attr in LOG_METHODS


def pii_arguments(call):
    """The `.email` / name attributes anywhere in this call's positional or
    keyword arguments (inside an f-string, %, + or .format too)."""
    found = []
    for arg in list(call.args) + [keyword.value for keyword in call.keywords]:
        for node in ast.walk(arg):
            if isinstance(node, ast.Attribute) and node.attr in PII_ATTRS:
                found.append(ast.unparse(node))
    return found


def is_production_file(path):
    """Not a test and not a migration: what the hook scans."""
    parts = path.split(os.sep)
    name = parts[-1]
    return (
        name.endswith(".py")
        and "migrations" not in parts
        and "tests" not in parts
        and not name.startswith(("test_", "tests_"))
        and name != "tests.py"
    )


def production_files(top):
    for folder, _, names in os.walk(os.path.join(settings.BASE_DIR, top)):
        for name in sorted(names):
            path = os.path.relpath(os.path.join(folder, name), settings.BASE_DIR)
            if is_production_file(path):
                yield path


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


class NoPiiInAnyLogCallTest(SimpleTestCase):
    """H-91: the hook's rule, over every production file of the cleaned
    apps."""

    def test_no_log_or_print_call_passes_an_address_or_a_name(self):
        problems = []
        for top in PII_GUARDED_DIRS:
            for path in production_files(top):
                with open(
                    os.path.join(settings.BASE_DIR, path), encoding="utf-8"
                ) as fh:
                    tree = ast.parse(fh.read())
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call) and is_log_or_print_call(node):
                        for leak in pii_arguments(node):
                            problems.append(f"{path}:{node.lineno}: {leak}")
        self.assertEqual(problems, [], "\n".join(problems))

    def test_the_rule_sees_each_shape_the_hook_does(self):
        samples = {
            'logger.info("x %s", user.email)': "user.email",
            'logger.info("x %s", sub.user.email)': "sub.user.email",
            'log.warning("x %s", request.user.email)': "request.user.email",
            'self.logger.error("x %s", row.first_name)': "row.first_name",
            'logging.info("x %s", row.last_name)': "row.last_name",
            'print(f"x {student.get_full_name()}")': "student.get_full_name",
            'print("x", student.get_full_name)': "student.get_full_name",
            'logger.info(f"x {user.email}")': "user.email",
            'logger.info("x %s" % user.email)': "user.email",
            'logger.info("x {}".format(user.email))': "user.email",
            'logger.info("x", extra={"who": user.email})': "user.email",
            'logger.log(20, "x %s", user.email)': "user.email",
            'logger.exception("x %s", user.email)': "user.email",
        }
        for code, expected in samples.items():
            with self.subTest(code=code):
                call = ast.parse(code, mode="eval").body
                self.assertTrue(is_log_or_print_call(call))
                self.assertIn(expected, pii_arguments(call))

    def test_the_rule_allows_what_the_hook_allows(self):
        """Ids, exception text and built messages are outside this rule."""
        for clean in (
            'logger.info("x %s", user.id)',
            'logger.error("x %s", exc)',
            'logger.info(f"x {user.id}")',
            'print("done", count)',
            'logger.info("x %s", recipient)',
        ):
            with self.subTest(clean=clean):
                call = ast.parse(clean, mode="eval").body
                self.assertTrue(is_log_or_print_call(call))
                self.assertEqual(pii_arguments(call), [])
        for not_a_log_call in ("send(user.email)", "queue.put(user.email)"):
            with self.subTest(code=not_a_log_call):
                self.assertFalse(
                    is_log_or_print_call(ast.parse(not_a_log_call, mode="eval").body)
                )

    def test_tests_and_migrations_are_not_scanned(self):
        for path, expected in {
            "billing/services.py": True,
            "billing/management/commands/x.py": True,
            "billing/tests/test_x.py": False,
            "billing/migrations/0001_initial.py": False,
            "users/tests_signals.py": False,
            "users/tests.py": False,
            "users/test_views.py": False,
        }.items():
            with self.subTest(path=path):
                self.assertEqual(is_production_file(path), expected)


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
