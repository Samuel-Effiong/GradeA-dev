"""
H-91: no log or print call anywhere in the repository passes an email
address or a person's name.

A repository-wide guard, so it lives under AutoGrader/ with the others (a
slice that never runs billing's tests still runs this one).

The rule is the one Epic A's check-no-pii-in-logs hook enforces
(scripts/check_no_pii_in_logs.py there), on purpose, so that a file which
passes here can leave the epic's baseline at the next merge-down:
  * a call to print(), or to .debug/.info/.warning/.warn/.error/
    .exception/.critical/.log on any receiver;
  * with `.email`, `.first_name`, `.last_name` or `.get_full_name` anywhere
    in a positional or keyword argument (inside an f-string, %, + or
    .format too);
  * in every .py file that is not a test module or a migration, scripts/
    included.
Exception text and messages that are not plain literals are outside this
rule; H-89's log scrubber covers them. billing/license_service.py and
users/signals.py are also held to H-80's stricter rule, in
billing/tests/test_logs_carry_no_email.py.
"""

import ast
import os

from django.conf import settings
from django.test import SimpleTestCase

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
#: H-91 has cleaned the whole repository, so the rule covers every
#: production file in it, as the hook does.
PII_GUARDED_DIRS = (".",)
#: Directories never scanned (the hook's list, plus hidden directories).
PII_SKIPPED_DIRS = {"migrations", "node_modules", "venv"}


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
    """Not a test module and not a migration: what the hook scans (a helper
    module inside a tests/ package is scanned, as the hook scans it)."""
    parts = path.split(os.sep)
    name = parts[-1]
    return (
        name.endswith(".py")
        and "migrations" not in parts
        and not name.startswith(("test_", "tests_"))
        and name != "tests.py"
    )


def production_files(top):
    for folder, subfolders, names in os.walk(os.path.join(settings.BASE_DIR, top)):
        subfolders[:] = sorted(
            name
            for name in subfolders
            if name not in PII_SKIPPED_DIRS and not name.startswith(".")
        )
        for name in sorted(names):
            path = os.path.relpath(os.path.join(folder, name), settings.BASE_DIR)
            if is_production_file(path):
                yield path


class NoPiiInAnyLogCallTest(SimpleTestCase):
    """H-91: the hook's rule, over every production file in the
    repository."""

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
            "billing/tests/helpers.py": True,
            "billing/migrations/0001_initial.py": False,
            "users/tests_signals.py": False,
            "users/tests.py": False,
            "users/test_views.py": False,
            "scripts/one_off_backfill_stripe_schedules.py": True,
            "assignments/tasks.py": True,
        }.items():
            with self.subTest(path=path):
                self.assertEqual(is_production_file(path), expected)
