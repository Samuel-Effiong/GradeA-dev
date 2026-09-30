"""
A coded failure (FR-A-06's CodedError) must survive being serialized, as
Celery does to the exception a task fails with.

Production's result serializer is json: Celery stores
{exc_type, exc_module, exc_message: exc.args} and rebuilds the exception as
cls(*args). CodedError used to render its message into args and take its
params keyword-only, so cls(<message>) failed, and Celery silently put a
plain Exception in its place: the class and the reason code were lost.
Pickle (eager tasks, tests) failed the same way.

No user-facing route reads a stored task result today (the status route
serves the tracked task row, written from the live exception; upload
refusals are returned as dicts; only .state is read), so nothing on staging
showed it. It matters from S6d on, when RUBRIC_MISSING leaves
grade_engine_async as a raised coded error.

Covered here, for every CodedError subclass found by reflection (so a new
one is covered without being listed), and for one defined only in this
module:
  - pickle at every protocol, and copy/deepcopy;
  - Celery's prepare_exception -> json wire -> exception_to_python, under
    json (production) and pickle;
  - a real task that raises one, run through Celery;
  - personal data: the stored form carries nothing the stored message did
    not already carry, no param is an email or an id, and stored results
    expire.

v2's probe (tests_vf2_codederror_pickle_probe.py) is the reproduce-first:
on b2890d9 it fails for all 7 classes under both serializers.
"""

import ast
import copy
import importlib
import json
import pickle
from pathlib import Path
from typing import Any
from unittest.mock import patch

from celery import shared_task
from django.apps import apps
from django.conf import settings
from django.test import SimpleTestCase

from audit.enums import ReasonCode
from AutoGrader.celery import app as celery_app
from AutoGrader.reason_codes import REASON_CODES, CodedError, coded_body

JSON_AND_PICKLE = ("json", "pickle")


class DefinedOnlyHere(CodedError):
    """A subclass no app module knows about: a fix on the base class covers
    it, a per-class list would not. Module-level so pickle can find it."""

    reason_code = ReasonCode.STUDENT_NOT_ON_ROSTER


@shared_task(name="AutoGrader.tests_codederror_serialization.fail_with_a_coded_error")
def fail_with_a_coded_error():
    raise DefinedOnlyHere(
        params={"file_name": "p07.pdf", "student_display": "Ada Obi"},
        detail="server-side",
    )


def _all_subclasses(cls):
    for sub in cls.__subclasses__():
        yield sub
        yield from _all_subclasses(sub)


def coded_classes():
    for config in apps.get_app_configs():
        for module in ("exceptions", "errors", "uploads"):
            try:
                importlib.import_module(f"{config.name}.{module}")
            except ImportError:
                pass
    return sorted(
        {
            cls
            for cls in _all_subclasses(CodedError)
            if getattr(cls, "reason_code", None) in REASON_CODES
        },
        key=lambda cls: cls.__qualname__,
    )


def sample(cls, detail="server-side"):
    spec = REASON_CODES[cls.reason_code]
    params = {
        name: 460 if name in ("actual", "limit") else f"<{name}>"
        for name in spec.placeholders()
    }
    if "dimension" in spec.params:
        params["dimension"] = "bytes"
    display = (
        {"actual": "460 bytes", "limit": "10 bytes"}
        if {"actual", "limit"} <= spec.placeholders()
        else None
    )
    return cls(params=params, detail=detail, display=display)


def through_celery(error, serializer):
    # The project's app, which the workers run, not celery.current_app: in
    # a full test run another module can make a default app current, with
    # other settings (it gave the 1-day default expiry). Typed as celery's
    # bare base class; the configured backend has these methods.
    backend: Any = celery_app.backend
    with patch.object(backend, "serializer", serializer):
        stored = backend.prepare_exception(error)
        if serializer == "json":
            stored = json.loads(json.dumps(stored))  # the real wire trip
        return stored, backend.exception_to_python(stored)


class CodedErrorSerializationTests(SimpleTestCase):
    def assert_same(self, original, rebuilt):
        self.assertIs(type(rebuilt), type(original))
        self.assertEqual(rebuilt.reason_code, original.reason_code)
        self.assertEqual(rebuilt.params, original.params)
        self.assertEqual(str(rebuilt), str(original))
        if hasattr(original, "status_code"):
            self.assertEqual(rebuilt.status_code, original.status_code)
        self.assertEqual(
            coded_body(rebuilt.reason_code, rebuilt.params, rebuilt.message),
            coded_body(original.reason_code, original.params, original.message),
        )

    def test_reflection_finds_every_coded_class(self):
        names = {cls.__qualname__ for cls in coded_classes()}
        for expected in (
            "SubmissionEmptyError",
            "FileUnreadableError",
            "FileTypeUnsupportedError",
            "FileTooLargeError",
            "PayloadTooLarge",
            "StudentNameUnmatchedError",
            "StudentNotOnRosterError",
            "DefinedOnlyHere",
        ):
            self.assertIn(expected, names)

    def test_pickle_at_every_protocol_and_copy(self):
        for cls in coded_classes():
            error = sample(cls)
            for protocol in range(2, pickle.HIGHEST_PROTOCOL + 1):
                with self.subTest(cls=cls.__qualname__, protocol=protocol):
                    rebuilt = pickle.loads(pickle.dumps(error, protocol=protocol))
                    self.assert_same(error, rebuilt)
                    # pickle restores __dict__, so the log-only detail stays.
                    self.assertEqual(rebuilt.__dict__, error.__dict__)
            with self.subTest(cls=cls.__qualname__, copy=True):
                self.assert_same(error, copy.copy(error))
                self.assert_same(error, copy.deepcopy(error))

    def test_through_celerys_result_backend_both_serializers(self):
        for serializer in JSON_AND_PICKLE:
            for cls in coded_classes():
                error = sample(cls)
                with self.subTest(cls=cls.__qualname__, serializer=serializer):
                    _stored, rebuilt = through_celery(error, serializer)
                    self.assert_same(error, rebuilt)

    def test_a_display_override_survives_json(self):
        """The message shows "460 bytes", not the raw int, after a rebuild."""
        from AutoGrader.uploads import PayloadTooLarge

        error = sample(PayloadTooLarge)
        _stored, rebuilt = through_celery(error, "json")
        self.assertIn("460 bytes", str(rebuilt))
        self.assertEqual(str(rebuilt), str(error))

    def test_a_real_task_that_fails_with_one_keeps_it(self):
        outcome = fail_with_a_coded_error.apply()
        self.assertTrue(outcome.failed())
        error = outcome.result
        assert isinstance(error, DefinedOnlyHere), error  # narrows for mypy
        self.assertEqual(error.reason_code, ReasonCode.STUDENT_NOT_ON_ROSTER)
        self.assertEqual(error.params["file_name"], "p07.pdf")


#: The one exception to "no param is an email" (SM ruling, 2026-10-01, Epic
#: A S7d): the four QA-approved codes whose text names the address. They are
#: item codes of SYNC routes (a roster import's rows, a licence's teachers),
#: answered only to the requester in a result list, never raised - so never
#: in a task's stored exception or result. The guard below keeps it so.
SYNC_ONLY_EMAIL_CODES = frozenset(
    {
        ReasonCode.ROW_EMAIL_INVALID,
        ReasonCode.TEACHER_EMAIL_NOT_BUSINESS,
        ReasonCode.TEACHER_HAS_INDIVIDUAL_SUBSCRIPTION,
        ReasonCode.TEACHER_ALREADY_ON_LICENCE,
    }
)

#: What builds those codes' entries: a task must not call them either.
SYNC_ONLY_ENTRY_BUILDERS = frozenset({"import_roster", "add_teachers_batch"})

REPO = Path(settings.BASE_DIR)
TASK_DECORATORS = {"shared_task", "task"}


def _production_modules():
    for path in sorted(REPO.rglob("*.py")):
        rel = path.relative_to(REPO).as_posix()
        if (
            rel.startswith(("docs/", "static/", "media/", "node_modules/", "."))
            or "/migrations/" in rel
            or "/tests/" in rel
            or "site-packages" in rel
            or path.name == "tests.py"
            or path.name.startswith(("test_", "tests_"))
        ):
            continue
        yield rel, path.read_text()


def _names_in(node):
    """Every name, attribute and string constant under `node`."""
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            yield child.id
        elif isinstance(child, ast.Attribute):
            yield child.attr
        elif isinstance(child, ast.Constant) and isinstance(child.value, str):
            yield child.value


def _defines_a_task(tree):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                target = (
                    decorator.func if isinstance(decorator, ast.Call) else decorator
                )
                if (getattr(target, "id", None) or getattr(target, "attr", None)) in (
                    TASK_DECORATORS
                ):
                    return True
    return False


def sync_only_violations(sources):
    """[(module, why)] for a raise of a sync-only email code anywhere, and
    for a task module that names one of those codes or their builders."""
    codes = {code.value for code in SYNC_ONLY_EMAIL_CODES}
    found = []
    for rel, source in sources:
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Raise) and node.exc is not None:
                raised = codes.intersection(_names_in(node.exc))
                if raised:
                    found.append((rel, f"raises {sorted(raised)}"))
        if _defines_a_task(tree):
            named = (codes | SYNC_ONLY_ENTRY_BUILDERS).intersection(_names_in(tree))
            if named:
                found.append((rel, f"a task module names {sorted(named)}"))
    return found


class MessageVariantThroughCeleryTests(SimpleTestCase):
    """S7d: a picked message variant (INSUFFICIENT_CREDITS_MID_BATCH with
    nothing finished) keeps its wording through the result backend."""

    def test_the_variant_survives_json_and_pickle(self):
        from students.exceptions import InsufficientCreditsMidBatchError

        error = InsufficientCreditsMidBatchError(
            params={"completed": 0, "total": 4}, variant="none_finished"
        )
        for serializer in JSON_AND_PICKLE:
            with self.subTest(serializer=serializer):
                _stored, rebuilt = through_celery(error, serializer)
                self.assertIsInstance(rebuilt, InsufficientCreditsMidBatchError)
                self.assertEqual(str(rebuilt), str(error))
                self.assertEqual(rebuilt.params, {"completed": 0, "total": 4})


class StoredFormPersonalDataTests(SimpleTestCase):
    """The SM's conditions on storing params in the result backend."""

    def test_no_param_is_an_email_or_an_id(self):
        for code, spec in REASON_CODES.items():
            for name in spec.params:
                if code in SYNC_ONLY_EMAIL_CODES and name == "email":
                    continue
                with self.subTest(code=code, param=name):
                    self.assertNotIn("email", name)
                    self.assertFalse(name == "id" or name.endswith("_id"), name)

    def test_the_email_exception_is_exactly_the_four_approved_codes(self):
        with_email = {
            code for code, spec in REASON_CODES.items() if "email" in spec.params
        }
        self.assertEqual(with_email, SYNC_ONLY_EMAIL_CODES)

    def test_no_sync_only_email_code_is_raised_or_reached_from_a_task(self):
        self.assertEqual(sync_only_violations(_production_modules()), [])

    def test_the_guard_catches_what_it_is_meant_to(self):
        cases = {
            "raised directly": (
                "raise CodedError(ReasonCode.TEACHER_ALREADY_ON_LICENCE)\n",
                1,
            ),
            "raised by its string": ("raise CodedError('ROW_EMAIL_INVALID')\n", 1),
            "a task names a code": (
                "@shared_task\ndef t():\n    x = ReasonCode.ROW_EMAIL_INVALID\n",
                1,
            ),
            "a task calls a builder": (
                "@app.task(bind=True)\ndef t(self):\n    import_roster(course=1)\n",
                1,
            ),
            "built, not raised, outside a task": (
                "e = CodedError(ReasonCode.ROW_EMAIL_INVALID)\n",
                0,
            ),
        }
        for label, (source, expected) in cases.items():
            with self.subTest(label):
                self.assertEqual(
                    len(sync_only_violations([("m.py", source)])), expected
                )

    def test_the_stored_form_adds_nothing_the_message_does_not_carry(self):
        """Every param that is a placeholder is already in the stored
        message; the only one that is not ('dimension') names a unit. The
        log-only detail is not stored under json at all."""
        for cls in coded_classes():
            error = sample(cls, detail="secret-server-detail")
            stored, _rebuilt = through_celery(error, "json")
            wire = json.dumps(stored)
            spec = REASON_CODES[cls.reason_code]
            with self.subTest(cls=cls.__qualname__):
                self.assertNotIn("secret-server-detail", wire)
                self.assertNotIn("@", wire)
                for name, value in error.params.items():
                    if name in spec.placeholders() and not (
                        error.args[3] and name in error.args[3]
                    ):
                        self.assertIn(str(value), str(error))
                    else:
                        self.assertIn(name, {"dimension", "actual", "limit"})

    def test_stored_results_expire(self):
        expires = celery_app.conf.result_expires
        self.assertEqual(expires, settings.CELERY_RESULT_EXPIRES)
        seconds = getattr(expires, "total_seconds", lambda: expires)()
        self.assertLessEqual(seconds, 24 * 3600)
