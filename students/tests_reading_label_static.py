"""AI-call record, slice 2: static checks on who writes what.

Each check holds a list that NAMES every writer of a result, with its reason;
a writer that is not on the list fails the test (ruling 1). They read the
source, so a future route that assigns a paper's answers, or writes a label
column by hand, or calls the extraction without handing on the run, fails
here and has to choose a fixed word.

  * the six label columns are written by one module (`students/reading_label`)
    and named by nothing else in the project's code;
  * `StudentSubmission.answers` is assigned only by the two save routes (and
    the load script, which keeps the placeholder);
  * both routes hand the extraction a run and apply the label from it;
  * every provider-call site of an enforced step task type is inside a scope;
  * the reading path calls no cache;
  * no serializer exposes a reading column.
"""

import ast
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase
from rest_framework import serializers as drf

from ai_processor import step_run
from students.models import StudentSubmission
from students.tests_grading_label_not_exposed import (
    _is_for_a_submission,
    _serializer_classes,
)

BASE = Path(settings.BASE_DIR)
APPS = (
    "AutoGrader",
    "ai_processor",
    "assignments",
    "audit",
    "billing",
    "classrooms",
    "dashboard",
    "students",
    "users",
)
READING_LABEL_FIELDS = (
    "answers_model",
    "answers_prompt_version",
    "answers_config_version",
    "answers_release",
    "answers_recheck_model",
    "answers_recheck_result",
)


def production_files():
    for app in APPS:
        for path in (BASE / app).rglob("*.py"):
            relative = path.relative_to(BASE).as_posix()
            if (
                "/tests" in relative
                or "/test_" in relative
                or "/migrations/" in relative
                or relative.startswith("ai_processor/benchmark/")
                or relative.endswith("conftest.py")
            ):
                continue
            yield relative, path


def parsed(relative):
    return ast.parse((BASE / relative).read_text(encoding="utf-8"))


class TheSixColumnsHaveOneWriterTest(SimpleTestCase):
    #: file -> reason it may name a column.
    MAY_NAME = {
        "students/models.py": "declares the columns",
        "students/reading_label.py": "the one writer: builds and applies the six",
        "students/admin.py": "lists them as read-only",
        "ai_processor/reading_run.py": "names the keys of the dict it builds",
    }

    def test_only_the_listed_files_name_a_reading_column(self):
        found = {}
        for relative, path in production_files():
            text = path.read_text(encoding="utf-8")
            hits = [name for name in READING_LABEL_FIELDS if name in text]
            if hits:
                found[relative] = hits
        self.assertEqual(set(found) - set(self.MAY_NAME), set(), found)

    def test_the_two_save_routes_name_none_of_them_literally(self):
        text = (BASE / "students/services.py").read_text(encoding="utf-8")
        for name in READING_LABEL_FIELDS:
            with self.subTest(name=name):
                self.assertNotIn(name, text)

    def test_no_code_assigns_a_column_by_attribute_outside_the_one_module(self):
        for relative, _path in production_files():
            if relative in ("students/reading_label.py",):
                continue
            for node in ast.walk(parsed(relative)):
                if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                    targets = (
                        node.targets if isinstance(node, ast.Assign) else [node.target]
                    )
                    for target in targets:
                        if (
                            isinstance(target, ast.Attribute)
                            and target.attr in READING_LABEL_FIELDS
                            and relative != "students/models.py"
                        ):
                            self.fail(f"{relative}:{node.lineno} assigns {target.attr}")

    def test_the_label_never_enters_the_grading_result_fields(self):
        from students.services import GRADING_RESULT_FIELDS

        self.assertEqual(set(GRADING_RESULT_FIELDS) & set(READING_LABEL_FIELDS), set())


class AnswersHaveTwoWritersTest(SimpleTestCase):
    """Every site that assigns a paper's `answers`, found by reading the
    code: attribute assignments, `answers=` handed to a model constructor,
    create, update or bulk call, and the word in an update_fields list."""

    #: file -> (functions that may write, reason)
    WRITERS = {
        "students/services.py": (
            {"upload_answers_engine", "update_submission_from_raw_text"},
            "the two routes that save a reading; both apply the label",
        ),
        "classrooms/scale_my_students.py": (
            None,
            "load script: makes rows with no reading, so the label stays "
            "'unlabelled'",
        ),
    }
    CALLS = {"StudentSubmission", "create", "update", "bulk_create", "bulk_update"}

    def _sites(self, relative):
        tree = parsed(relative)
        sites = []

        def visit(node, function):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                function = node.name
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Attribute) and target.attr == "answers":
                        sites.append((function, node.lineno))
            if isinstance(node, ast.Call):
                callee = (
                    node.func.id
                    if isinstance(node.func, ast.Name)
                    else getattr(node.func, "attr", "")
                )
                if callee in self.CALLS and any(
                    k.arg == "answers" for k in node.keywords
                ):
                    sites.append((function, node.lineno))
                for keyword in node.keywords:
                    if keyword.arg == "update_fields" and isinstance(
                        keyword.value, (ast.List, ast.Tuple)
                    ):
                        if any(
                            isinstance(e, ast.Constant) and e.value == "answers"
                            for e in keyword.value.elts
                        ):
                            sites.append((function, node.lineno))
            for child in ast.iter_child_nodes(node):
                visit(child, function)

        visit(tree, None)
        return sites

    def test_only_the_listed_files_write_answers(self):
        writers = {}
        for relative, _ in production_files():
            sites = self._sites(relative)
            if sites:
                writers[relative] = sites
        self.assertEqual(set(writers) - set(self.WRITERS), set(), writers)

    def test_in_the_services_only_the_two_routes_write_them(self):
        allowed = self.WRITERS["students/services.py"][0] or set()
        sites = self._sites("students/services.py")
        self.assertTrue(sites)
        self.assertEqual({function for function, _ in sites} - allowed, set())

    def test_each_route_applies_the_label_from_its_run(self):
        tree = parsed("students/services.py")
        functions = {
            n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
        }
        for name in ("upload_answers_engine", "update_submission_from_raw_text"):
            with self.subTest(route=name):
                calls = [
                    c
                    for c in ast.walk(functions[name])
                    if isinstance(c, ast.Call)
                    and getattr(c.func, "id", getattr(c.func, "attr", ""))
                    == "apply_reading_label"
                ]
                self.assertEqual(len(calls), 1)

    def test_each_route_hands_the_extraction_a_run(self):
        tree = parsed("students/services.py")
        calls = [
            c
            for c in ast.walk(tree)
            if isinstance(c, ast.Call)
            and getattr(c.func, "attr", "") == "extract_answer_with_retry"
        ]
        self.assertEqual(len(calls), 2)
        for call in calls:
            with self.subTest(line=call.lineno):
                self.assertIn("reading_run", [k.arg for k in call.keywords])

    def test_every_other_caller_of_the_extraction_is_known(self):
        callers = set()
        for relative, _ in production_files():
            for node in ast.walk(parsed(relative)):
                if (
                    isinstance(node, ast.Call)
                    and getattr(node.func, "attr", "") == "extract_answer_with_retry"
                ):
                    callers.add(relative)
        self.assertEqual(callers, {"students/services.py"})


class EveryProviderCallOfAnEnforcedTypeIsInsideAScopeTest(SimpleTestCase):
    def test_slice_2_enforces_the_reading_and_only_it(self):
        self.assertEqual(
            step_run.ENFORCED_STEP_TASK_TYPES, frozenset({"extract_answer"})
        )

    def test_each_call_for_an_enforced_type_is_in_a_with_scope(self):
        tree = parsed("ai_processor/services.py")
        enforced = step_run.ENFORCED_STEP_TASK_TYPES
        checked = []

        def visit(node, in_scope):
            if isinstance(node, ast.With):
                for item in node.items:
                    expr = item.context_expr
                    if (
                        isinstance(expr, ast.Call)
                        and getattr(expr.func, "attr", "") == "scope"
                    ):
                        in_scope = True
            if (
                isinstance(node, ast.Call)
                and getattr(node.func, "attr", "") == "execute_graded_task"
            ):
                for keyword in node.keywords:
                    if (
                        keyword.arg == "task_type"
                        and isinstance(keyword.value, ast.Constant)
                        and keyword.value.value in enforced
                    ):
                        checked.append(node.lineno)
                        self.assertTrue(
                            in_scope, f"line {node.lineno} is not in a scope"
                        )
            for child in ast.iter_child_nodes(node):
                visit(child, in_scope)

        visit(tree, False)
        # the single call, the chunked call and the re-check
        self.assertEqual(len(checked), 3, checked)


class TheReadingPathCallsNoCacheTest(SimpleTestCase):
    FUNCTIONS = (
        "_extract_answers_chunked",
        "extract_answer_image",
        "_verify_blank_answers",
        "extract_answer_with_retry",
    )

    def test_no_function_of_the_reading_names_a_cache(self):
        tree = parsed("ai_processor/services.py")
        found = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        for name in self.FUNCTIONS:
            with self.subTest(function=name):
                names = {
                    n.id for n in ast.walk(found[name]) if isinstance(n, ast.Name)
                } | {
                    n.attr
                    for n in ast.walk(found[name])
                    if isinstance(n, ast.Attribute)
                }
                self.assertNotIn("grading_cache", names)
                self.assertNotIn("cache", names)


class NoSerializerExposesAReadingColumnTest(SimpleTestCase):
    def test_no_submission_serializer_has_a_reading_field(self):
        for serializer_class in _serializer_classes():
            if not _is_for_a_submission(serializer_class):
                continue
            with self.subTest(serializer=serializer_class.__qualname__):
                listed = getattr(serializer_class.Meta, "fields", ())
                fields = set(listed) | set(serializer_class._declared_fields)
                self.assertEqual(fields & set(READING_LABEL_FIELDS), set())

    def test_no_serializer_anywhere_declares_a_field_named_like_one(self):
        for serializer_class in _serializer_classes():
            declared = set(getattr(serializer_class, "_declared_fields", {}))
            with self.subTest(serializer=serializer_class.__qualname__):
                self.assertEqual(declared & set(READING_LABEL_FIELDS), set())

    def test_no_serializer_file_names_a_reading_column(self):
        for relative in (
            "students/serializers.py",
            "students/second_opinion_serializers.py",
            "assignments/serializers.py",
            "classrooms/serializers.py",
            "dashboard/serializers.py",
        ):
            path = BASE / relative
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8")
            for name in READING_LABEL_FIELDS:
                with self.subTest(file=relative, name=name):
                    self.assertNotIn(name, text)

    def test_the_walk_still_finds_the_submission_serializers(self):
        found = [c for c in _serializer_classes() if _is_for_a_submission(c)]
        self.assertGreaterEqual(len(found), 2)
        self.assertTrue(issubclass(found[0], drf.BaseSerializer))
        self.assertIs(found[0].Meta.model, StudentSubmission)
