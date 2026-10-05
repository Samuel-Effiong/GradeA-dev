"""
H-89: nothing this process logs carries an email address or a URL password.

H-80 and H-91 made the log calls pass ids. What they cannot reach is text
the code does not write: an exception's own message in a traceback (an
IntegrityError's "Key (email)=(...)", a ValueError built with an address),
or a third-party library's line. AutoGrader/log_scrubbing.py scrubs every
record at the point it is made, so it holds for every handler: the one in
settings, Python's last-resort handler, a Celery worker's.

The scrubber is OFF under the test runner (settings.LOG_SCRUB_ADDRESSES),
so that the tests which prove log lines carry ids keep reading unscrubbed
text. These tests switch it on.
"""

import ast
import io
import logging
import os
import subprocess
import sys
from unittest.mock import PropertyMock, patch

from celery.app.log import TaskFormatter
from celery.utils.log import get_task_logger
from django.conf import settings
from django.db import IntegrityError
from django.test import SimpleTestCase, override_settings

from AutoGrader import log_scrubbing

ADDRESS = "someone.private@school-example.edu"
CONSTRAINT = (
    'duplicate key value violates unique constraint "users_customuser_email_key"\n'
    f"DETAIL:  Key (email)=({ADDRESS}) already exists."
)


def standalone_logger(name, handler=None):
    """A logger outside the configured hierarchy: no parent, so only the
    handler given (or, with none, Python's last-resort handler) prints."""
    logger = logging.Logger(name)
    if handler is not None:
        logger.addHandler(handler)
    return logger


def stream_handler(formatter=None):
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(formatter or logging.Formatter("%(levelname)s %(message)s"))
    return handler, stream


class Keep(logging.Handler):
    """Keeps the records it is given."""

    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


def raise_and_log(logger, error, **kwargs):
    try:
        raise error
    except Exception:  # noqa: BLE001 - the test logs whatever it raised
        logger.error("Failed to renew credits for teacher %s", 4821, **kwargs)


@override_settings(LOG_SCRUB_ADDRESSES=True)
class EveryHandlerPrintsScrubbedTextTests(SimpleTestCase):
    def assertScrubbed(self, output):
        self.assertNotIn(ADDRESS, output)
        self.assertNotIn("@school-example", output)
        self.assertIn("[email]", output)

    def test_the_console_handler_from_settings(self):
        [console] = [
            handler
            for handler in logging.getLogger("django").handlers
            if isinstance(handler, logging.StreamHandler)
        ]
        stream = io.StringIO()
        previous = console.setStream(stream)
        self.addCleanup(console.setStream, previous)

        logging.getLogger("django").error("Refused %s (user %s)", ADDRESS, 4821)

        self.assertScrubbed(stream.getvalue())
        self.assertIn("(user 4821)", stream.getvalue())

    def test_pythons_last_resort_handler(self):
        """What prints a billing.* or users.* line when the process has
        configured no handler for it."""
        logger = standalone_logger("h89.unconfigured")
        with patch.object(sys, "stderr", io.StringIO()) as stderr:
            logger.warning("Refused %s (user %s)", ADDRESS, 4821)
            output = stderr.getvalue()

        self.assertScrubbed(output)
        self.assertIn("(user 4821)", output)

    def test_a_celery_task_loggers_own_formatter(self):
        handler, stream = stream_handler(
            TaskFormatter(
                "[%(asctime)s: %(levelname)s/%(processName)s] "
                "%(task_name)s[%(task_id)s]: %(message)s"
            )
        )
        logger = get_task_logger("h89.task")
        logger.addHandler(handler)
        self.addCleanup(logger.removeHandler, handler)
        previous = logger.propagate
        logger.propagate = False
        self.addCleanup(setattr, logger, "propagate", previous)

        raise_and_log(
            logger, ValueError(f"Teacher {ADDRESS} has a plan"), exc_info=True
        )

        output = stream.getvalue()
        self.assertScrubbed(output)
        self.assertIn("teacher 4821", output)
        self.assertIn("ValueError", output)

    def test_an_object_argument_whose_text_is_an_address(self):
        class Person:
            def __str__(self):
                return ADDRESS

        handler, stream = stream_handler()
        standalone_logger("h89", handler).info("Enrolled %s", Person())

        self.assertScrubbed(stream.getvalue())


@override_settings(LOG_SCRUB_ADDRESSES=True)
class ExceptionTextTests(SimpleTestCase):
    """The two leak shapes of the 13 traceback sites (H-80's known limit)."""

    def output_of(self, error, **kwargs):
        handler, stream = stream_handler()
        raise_and_log(standalone_logger("h89", handler), error, **kwargs)
        return stream.getvalue()

    def assertTracebackWithoutAddress(self, output, class_name, frame="raise_and_log"):
        self.assertIn("Traceback (most recent call last)", output)
        self.assertIn(frame, output, "the frames are kept")
        self.assertIn(class_name, output)
        self.assertIn("teacher 4821", output)
        self.assertNotIn(ADDRESS, output)
        self.assertIn("[email]", output)

    def test_a_constraint_error_naming_the_address(self):
        output = self.output_of(IntegrityError(CONSTRAINT), exc_info=True)

        self.assertTracebackWithoutAddress(output, "IntegrityError")
        self.assertIn("Key (email)=([email]) already exists.", output)

    def test_an_error_built_with_an_address(self):
        output = self.output_of(
            ValueError(f"Teacher {ADDRESS} does not belong to this school."),
            exc_info=True,
        )

        self.assertTracebackWithoutAddress(output, "ValueError")

    def test_logger_exception(self):
        handler, stream = stream_handler()
        logger = standalone_logger("h89", handler)
        try:
            raise ValueError(f"Teacher {ADDRESS} has a plan")
        except ValueError:
            logger.exception("Failed for teacher %s", 4821)

        self.assertTracebackWithoutAddress(
            stream.getvalue(), "ValueError", frame="test_logger_exception"
        )

    def test_a_chained_exception(self):
        handler, stream = stream_handler()
        logger = standalone_logger("h89", handler)
        try:
            try:
                raise IntegrityError(CONSTRAINT)
            except IntegrityError as exc:
                raise ValueError(f"Could not invite {ADDRESS}") from exc
        except ValueError:
            logger.error("Invitation failed for user %s", 4821, exc_info=True)

        output = stream.getvalue()
        self.assertNotIn(ADDRESS, output)
        self.assertIn("IntegrityError", output)
        self.assertIn("ValueError", output)
        self.assertEqual(output.count("[email]"), 2)


@override_settings(LOG_SCRUB_ADDRESSES=True)
class UrlCredentialsTests(SimpleTestCase):
    def output_of(self, text):
        handler, stream = stream_handler()
        standalone_logger("h89", handler).warning("Cannot connect to %s", text)
        return stream.getvalue()

    def test_a_dsn_never_prints_its_password(self):
        password = "s3cret-pass"  # pragma: allowlist secret
        for dsn in (
            f"redis://:{password}@redis:6379/0",  # a dotless host
            f"redis://default:{password}@localhost:6379/0",
            f"rediss://default:{password}@cache.internal.example.com:6379/0",
            f"postgres://grader:{password}@db.example.com:5432/grader",
            f"amqp://guest:{password}@rabbit//",
        ):
            with self.subTest(dsn=dsn):
                output = self.output_of(dsn)
                self.assertNotIn(password, output)
                self.assertIn("://[credentials]@", output)
                # The line still says where it was connecting to.
                self.assertIn(dsn.split("@", 1)[1], output)

    def test_a_password_with_an_at_sign_in_it(self):
        """The userinfo ends at the LAST "@" before the host, not the
        first: on a dotless host nothing else would catch the rest."""
        head, tail = "AB12", "CD34"
        for dsn in (
            f"redis://default:{head}@{tail}@redis:6379/0",
            f"redis://:{head}@{tail}@localhost",
            f"postgres://grader:{head}@{tail}@db.example.com:5432/grader",
        ):
            with self.subTest(host=dsn.rsplit("@", 1)[1]):
                output = self.output_of(dsn)
                self.assertNotIn(head, output)
                self.assertNotIn(tail, output)
                self.assertIn("://[credentials]@" + dsn.rsplit("@", 1)[1], output)

    def test_a_url_without_credentials_is_left_alone(self):
        for url in (
            "https://api.example.com/v1/items?x=1",
            "redis://redis:6379/0",
        ):
            with self.subTest(url=url):
                self.assertIn(url, self.output_of(url))

    def test_an_address_in_a_query_string(self):
        output = self.output_of(f"https://app.example.com/register?email={ADDRESS}&t=1")

        self.assertNotIn(ADDRESS, output)
        self.assertIn("https://app.example.com/register?email=[email]&t=1", output)

    def test_text_that_only_looks_like_an_address_is_left_alone(self):
        for text in ("    @transaction.atomic", "pkg@1.2.3", "a @ b", "x@y"):
            with self.subTest(text=text):
                self.assertIn(text, self.output_of(text))


@override_settings(LOG_SCRUB_ADDRESSES=True)
class TheRecordItselfTests(SimpleTestCase):
    def test_msg_args_and_exc_info_are_left_as_they_were(self):
        """Only getMessage() and the rendered exception text are scrubbed;
        anything that reads the record's parts directly still gets them."""
        keep = Keep()
        error = ValueError(f"Teacher {ADDRESS} has a plan")
        raise_and_log(standalone_logger("h89", keep), error, exc_info=True)
        [record] = keep.records

        self.assertEqual(record.msg, "Failed to renew credits for teacher %s")
        self.assertEqual(record.args, (4821,))
        self.assertIs(record.exc_info[1], error)
        self.assertNotIn(ADDRESS, record.exc_text)

    def test_a_message_that_cannot_be_formatted_fails_closed(self):
        """The template and a marker; never the arguments, and no error
        into the caller."""
        handler, stream = stream_handler()

        standalone_logger("h89", handler).info("Enrolled %d of %s", ADDRESS)

        output = stream.getvalue()
        self.assertIn("Enrolled %d of %s", output)
        self.assertIn("[log arguments withheld", output)
        self.assertNotIn(ADDRESS, output)

    def test_a_record_still_pickles(self):
        import pickle

        keep = Keep()
        standalone_logger("h89", keep).info("Refused %s", ADDRESS)
        [record] = keep.records

        copy = pickle.loads(pickle.dumps(record))  # nosec B301 - our own bytes

        self.assertNotIn(ADDRESS, copy.getMessage())


class TheSwitchTests(SimpleTestCase):
    def test_the_factory_is_installed(self):
        self.assertTrue(
            getattr(logging.getLogRecordFactory(), "_scrubs_addresses", False),
            "settings did not install the scrubbing log record factory",
        )

    def test_it_is_off_under_the_test_runner(self):
        """So H-80's and H-91's tests read what the code really logged."""
        self.assertIs(getattr(settings, "LOG_SCRUB_ADDRESSES", None), False)
        handler, stream = stream_handler()

        standalone_logger("h89", handler).info("Refused %s", ADDRESS)

        self.assertIn(ADDRESS, stream.getvalue())

    def test_override_settings_switches_it_on_and_back_off(self):
        handler, stream = stream_handler()
        logger = standalone_logger("h89", handler)

        with override_settings(LOG_SCRUB_ADDRESSES=True):
            logger.info("first %s", ADDRESS)
        logger.info("second %s", ADDRESS)

        first, second = stream.getvalue().splitlines()
        self.assertNotIn(ADDRESS, first)
        self.assertIn(ADDRESS, second)

    def test_no_environment_or_variable_can_switch_it_off(self):
        """In settings.py the switch is computed from "are tests running"
        alone: no env var, and no ENVIRONMENT branch."""
        with open(os.path.join(settings.BASE_DIR, "AutoGrader", "settings.py")) as fh:
            tree = ast.parse(fh.read())
        [assignment] = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "LOG_SCRUB_ADDRESSES"
                for target in node.targets
            )
        ]
        self.assertEqual(assignment.col_offset, 0, "it is set inside a branch")
        names = {
            node.id for node in ast.walk(assignment.value) if isinstance(node, ast.Name)
        }
        self.assertNotIn("env", names)
        self.assertNotIn("ENVIRONMENT", names)
        self.assertEqual(ast.unparse(assignment.value), "not _TESTS_ARE_RUNNING")


class SettingsStandAloneTests(SimpleTestCase):
    """settings.py must still load on its own, by path, with the project
    not importable (the frontend-domain setting tests load it that way).
    The first version of H-89 imported log_scrubbing from settings.py and
    broke exactly that."""

    def test_settings_import_nothing_from_the_project_at_the_top_level(self):
        with open(os.path.join(settings.BASE_DIR, "AutoGrader", "settings.py")) as fh:
            tree = ast.parse(fh.read())
        project = ("AutoGrader", "billing", "users", "classrooms", "assignments")
        imported = []
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
            elif isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
        self.assertEqual(
            [name for name in imported if name.split(".")[0] in project], []
        )

    def test_the_package_installs_the_factory(self):
        with open(os.path.join(settings.BASE_DIR, "AutoGrader", "__init__.py")) as fh:
            tree = ast.parse(fh.read())
        calls = [
            ast.unparse(node.value)
            for node in tree.body
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
        ]
        self.assertEqual(calls, ["_install_log_scrubbing()"])
        # ... before the Celery app, the first thing in the package that
        # can log.
        first_two = [type(node).__name__ for node in tree.body[:2]]
        self.assertEqual(first_two, ["ImportFrom", "Expr"])


class BeforeSettingsAreConfiguredTests(SimpleTestCase):
    def setUp(self):
        self.addCleanup(log_scrubbing.set_enabled, log_scrubbing.is_enabled())

    def test_a_record_made_while_settings_are_loading_is_scrubbed(self):
        log_scrubbing.set_enabled(None)
        handler, stream = stream_handler()
        with patch.object(
            type(settings), "configured", new_callable=PropertyMock, return_value=False
        ):
            self.assertTrue(log_scrubbing.is_enabled())
            standalone_logger("h89", handler).info("Refused %s", ADDRESS)

        self.assertNotIn(ADDRESS, stream.getvalue())

    def test_once_settings_are_configured_the_switch_is_theirs(self):
        log_scrubbing.set_enabled(None)

        self.assertIs(log_scrubbing.is_enabled(), settings.LOG_SCRUB_ADDRESSES)
        with override_settings(LOG_SCRUB_ADDRESSES=True):
            self.assertTrue(log_scrubbing.is_enabled())
        self.assertFalse(log_scrubbing.is_enabled())


class OutsideTheTestRunnerTests(SimpleTestCase):
    """A real process that is not running tests: a management command's
    start, before Django's own logging configuration is applied. Importing
    the AutoGrader package is what installs the factory, as it is for a
    web process (AutoGrader.wsgi) and a Celery worker (-A AutoGrader)."""

    SCRIPT = """
import logging, os, sys
sys.argv = ["manage.py", "shell"]
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "AutoGrader.settings")
from AutoGrader import settings
logging.getLogger("billing.early").warning("Refused %s (user %s)", sys.stdin.readline().strip(), 4821)
print(settings.LOG_SCRUB_ADDRESSES, getattr(logging.getLogRecordFactory(), "_scrubs_addresses", False))
import django
django.setup()
print(django.conf.settings.LOG_SCRUB_ADDRESSES, getattr(logging.getLogRecordFactory(), "_scrubs_addresses", False))
from AutoGrader import log_scrubbing
print(log_scrubbing.is_enabled())
"""

    def test_it_is_on_and_scrubs_from_the_first_record(self):
        result = subprocess.run(
            [sys.executable, "-c", self.SCRIPT],
            cwd=str(settings.BASE_DIR),
            input=ADDRESS + "\n",
            capture_output=True,
            text=True,
            timeout=120,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.split(), ["True"] * 5)
        self.assertIn("Refused [email] (user 4821)", result.stderr)
        self.assertNotIn(ADDRESS, result.stderr)


class InstallTests(SimpleTestCase):
    """install() wraps whatever factory is in place, once."""

    def setUp(self):
        self.addCleanup(logging.setLogRecordFactory, logging.getLogRecordFactory())
        self.addCleanup(log_scrubbing.set_enabled, log_scrubbing.is_enabled())

    def test_a_second_install_does_not_wrap_again(self):
        before = logging.getLogRecordFactory()

        log_scrubbing.install(False)
        log_scrubbing.install(False)

        self.assertIs(logging.getLogRecordFactory(), before)
        wrapped = getattr(before, "wrapped", None)
        self.assertFalse(getattr(wrapped, "_scrubs_addresses", False))

    def test_a_second_install_still_sets_the_switch(self):
        log_scrubbing.install(True)
        self.assertTrue(log_scrubbing.is_enabled())
        log_scrubbing.install(False)
        self.assertFalse(log_scrubbing.is_enabled())

    def test_another_factorys_records_keep_their_class_and_are_scrubbed(self):
        """Celery or Sentry may have set their own factory first."""

        class TheirRecord(logging.LogRecord):
            theirs = True

        def their_factory(*args, **kwargs):
            record = TheirRecord(*args, **kwargs)
            record.stamped = "by their factory"
            return record

        logging.setLogRecordFactory(their_factory)
        log_scrubbing.install(True)
        keep = Keep()

        standalone_logger("h89", keep).info("Refused %s", ADDRESS)

        [record] = keep.records
        self.assertIsInstance(record, TheirRecord)
        self.assertEqual(record.stamped, "by their factory")
        self.assertTrue(record.theirs)
        self.assertEqual(record.getMessage(), "Refused [email]")
        self.assertIs(
            getattr(logging.getLogRecordFactory(), "wrapped", None), their_factory
        )

    def test_a_record_whose_class_cannot_be_replaced_fails_closed(self):
        class Fixed(logging.LogRecord):
            def __setattr__(self, name, value):
                if name == "__class__":
                    raise TypeError("this record's class is fixed")
                super().__setattr__(name, value)

        logging.setLogRecordFactory(Fixed)
        log_scrubbing.install(True)
        keep = Keep()

        standalone_logger("h89", keep).info("Refused %s", ADDRESS)

        [record] = keep.records
        self.assertNotIn(ADDRESS, record.getMessage())
        self.assertIn("Refused %s", record.getMessage())
        self.assertIn(log_scrubbing.MESSAGE_WITHHELD, record.getMessage())

    def test_exception_text_that_cannot_be_rendered_fails_closed(self):
        log_scrubbing.install(True)
        keep = Keep()
        with patch.object(
            log_scrubbing.traceback, "format_exception", side_effect=RuntimeError
        ):
            raise_and_log(
                standalone_logger("h89", keep),
                ValueError(f"Teacher {ADDRESS} has a plan"),
                exc_info=True,
            )

        [record] = keep.records
        self.assertEqual(
            record.exc_text, f"ValueError: {log_scrubbing.EXCEPTION_WITHHELD}"
        )
        handler, stream = stream_handler()
        handler.handle(record)
        self.assertNotIn(ADDRESS, stream.getvalue())


class ScrubTests(SimpleTestCase):
    def test_text_without_an_at_sign_is_returned_as_it_is(self):
        text = "Refreshed monthly credits for teacher 4821 under license 77."
        self.assertIs(log_scrubbing.scrub(text), text)

    def test_addresses_of_many_shapes(self):
        for address in (
            "a@b.co",
            "first.last+tag@sub.school.edu",
            "UPPER_case-99%x@Example-School.ORG",
            # Local parts our own validation accepts (Django's validator).
            "o'brien@school.edu",
            "a!b#c$d%e*f+g^h_i`j{k|l}m~n-o@school.edu",
            "j\u00fcrgen@school.edu",
            # Domains that are not ASCII letters: written as they are read,
            # and in their encoded (punycode) form.
            "pupil@m\u00fcnchen.de",
            "pupil@xn--mnchen-3ya.de",
            "pupil@school.xn--p1ai",
            "pupil@\u0448\u043a\u043e\u043b\u0430.\u0440\u0444",
        ):
            with self.subTest(address=address):
                self.assertEqual(
                    log_scrubbing.scrub(f"<{address}>, ({address})"),
                    "<[email]>, ([email])",
                )

    def test_a_key_before_an_address_stays_readable(self):
        self.assertEqual(
            log_scrubbing.scrub("Key (email)=(o'brien@school.edu) already exists."),
            "Key (email)=([email]) already exists.",
        )
        self.assertEqual(
            log_scrubbing.scrub("invite failed: email=o'brien@school.edu, row=7"),
            "invite failed: email=[email], row=7",
        )

    def test_a_quote_brace_or_bar_just_before_an_address_goes_with_it(self):
        """They are characters an address may start with, so the scrubber
        cannot tell them from the address. Accepted (SM): too much is
        replaced, never too little."""
        for text, expected in (
            ("email='pupil@school.edu'", "email=[email]'"),
            ("{pupil@school.edu}", "[email]}"),
            ("|pupil@school.edu|", "[email]|"),
        ):
            with self.subTest(text=text):
                self.assertEqual(log_scrubbing.scrub(text), expected)

    def test_the_four_characters_an_address_is_not_followed_through(self):
        """A KNOWN LIMIT (SM, 2026-10-05). "/", "=", "?" and "&" are legal
        in an address's local part, and they are also what separates a key
        from its value and the parts of a URL. The scrubber stops at them,
        so `email=...` lines and URLs stay readable. An address that itself
        contains one keeps the piece before that character in print: a
        fragment, never a usable address."""
        for character in "/=?&":
            with self.subTest(character=character):
                self.assertEqual(
                    log_scrubbing.scrub(f"<left{character}right@school.edu>"),
                    f"<left{character}[email]>",
                )
