"""The per-call usage record (H-201): what the code is allowed to look like.

Static checks, read from the source, so that a later change cannot widen the
record, add a text column, drop a hook, or leave a new AI call unconsidered
without a test saying so:

  * the columns are pinned by name and type; no free text but the model names;
  * the hooks in `execute_graded_task` are exactly the decorator and four
    one-line notes, each next to the line it was placed by;
  * every `execute_graded_task` call site is listed as MARKED (its task type
    proves a kind) or NOT MARKED (kind NULL), and an unlisted site fails;
  * nothing reads or exposes the model: no route, serializer, admin, update;
  * the migration is one CreateModel, and it goes forward, back and forward.
"""

import ast
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.db import connection, models
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader
from django.test import SimpleTestCase, TransactionTestCase

from ai_processor import call_record
from ai_processor.models import AICallRecord

BASE = Path(settings.BASE_DIR)
SERVICES = "ai_processor/services.py"

COLUMNS = {
    "id": "UUIDField",
    "created_at": "DateTimeField",
    "task_id": "CharField",
    "task_type": "CharField",
    "feature": "CharField",
    "kind": "CharField",
    "outcome": "CharField",
    "error_class": "CharField",
    "model_asked": "CharField",
    "model_answered": "CharField",
    "upstream_provider": "CharField",
    "finish_reason": "CharField",
    "prompt_version": "CharField",
    "estimate": "PositiveBigIntegerField",
    "prompt_tokens": "PositiveBigIntegerField",
    "completion_tokens": "PositiveBigIntegerField",
    "reasoning_tokens": "PositiveBigIntegerField",
    "cached_tokens": "PositiveBigIntegerField",
    "total_tokens": "PositiveBigIntegerField",
    "charged_credits": "PositiveBigIntegerField",
    "cost_usd": "DecimalField",
    "images": "PositiveIntegerField",
    "pdf_items": "PositiveIntegerField",
    "questions": "PositiveIntegerField",
    "duration_ms": "PositiveIntegerField",
    "billed_user_id": "UUIDField",
    "school_id": "UUIDField",
    "assignment_id": "UUIDField",
    "processing_task_id": "UUIDField",
}
#: CharFields that are not a closed set: the provider's names, the billing id
#: (a uuid text) and the prompt version word.
OPEN_TEXT = {
    "model_asked",
    "model_answered",
    "upstream_provider",
    "task_id",
    "prompt_version",
}

#: Every execute_graded_task call site in ai_processor/services.py, by the
#: function it is in and its task type; True when the task type proves a kind.
CALL_SITES = {
    ("extract_assignment", "extract_assignment"): True,
    ("extract_assignment_image", "extract_assignment"): True,
    ("_extract_prosemirror_chunked", "extract_assignment"): True,
    ("_extract_assignment_chunked", "extract_assignment"): True,
    ("_extract_answers_chunked", "extract_answer"): False,
    ("extract_answer_image", "extract_answer"): False,
    ("_verify_blank_answers", "extract_answer"): False,
    ("_grade_question_batch", "grade_assignment"): False,
    ("_build_overall_grading_summary", "grade_assignment"): False,
    ("_grade_student_submission_impl", "grade_assignment"): False,
    ("generate_assignment_from_prompt", "generate_assignment"): True,
    ("formatted_grade", "formatted_grade"): True,
    ("custom_ai_prompt", None): False,
    ("generate_student_summary", "student_summary"): True,
    ("generate_weekly_course_summary_narrative", "weekly_course_summary"): True,
    (
        "generate_weekly_school_admin_summary_narrative",
        "weekly_school_admin_summary",
    ): True,
}


def _tree(relative):
    return ast.parse((BASE / relative).read_text(encoding="utf-8"))


def _function(tree, class_name, name):
    for cls in tree.body:
        if isinstance(cls, ast.ClassDef) and cls.name == class_name:
            for node in cls.body:
                if isinstance(node, ast.FunctionDef) and node.name == name:
                    return node
    raise AssertionError(f"{class_name}.{name} not found")


class TheColumns(SimpleTestCase):
    def test_the_columns_are_exactly_these_by_name_and_type(self):
        found = {f.name: type(f).__name__ for f in AICallRecord._meta.get_fields()}
        self.assertEqual(found, COLUMNS)

    def test_no_free_text_no_json_no_foreign_key(self):
        for field in AICallRecord._meta.get_fields():
            with self.subTest(field=field.name):
                self.assertNotIsInstance(field, (models.TextField, models.JSONField))
                self.assertFalse(field.is_relation)
                if isinstance(field, models.CharField) and field.name not in OPEN_TEXT:
                    self.assertTrue(field.choices, "a closed set")
                    if field.name in (
                        "task_type",
                        "feature",
                        "error_class",
                        "finish_reason",
                    ):
                        self.assertIn("other", [c[0] for c in (field.choices or [])])

    def test_every_text_column_is_capped(self):
        for field in AICallRecord._meta.get_fields():
            if isinstance(field, models.CharField):
                with self.subTest(field=field.name):
                    self.assertLessEqual(field.max_length or 0, 255)

    def test_the_upstream_provider_column_is_short_nullable_text(self):
        """The provider's name as the reply gave it: not content, so it may be text, but short and NULL when absent."""
        field = AICallRecord._meta.get_field("upstream_provider")
        self.assertIsInstance(field, models.CharField)
        self.assertEqual(field.max_length, 100)
        self.assertTrue(field.null)
        self.assertFalse(field.choices)

    def test_the_prompt_version_column_has_both_defaults(self):
        field = AICallRecord._meta.get_field("prompt_version")
        self.assertFalse(field.null)
        self.assertEqual(field.default, "unknown")
        self.assertEqual(field.db_default, "unknown")

    def test_the_numbers_may_be_absent_but_never_zero_for_unknown(self):
        for name in (
            "estimate",
            "prompt_tokens",
            "completion_tokens",
            "reasoning_tokens",
            "cached_tokens",
            "total_tokens",
            "cost_usd",
            "images",
            "questions",
        ):
            with self.subTest(name=name):
                field = next(
                    f for f in AICallRecord._meta.concrete_fields if f.name == name
                )
                self.assertTrue(field.null)
                self.assertFalse(field.has_default())

    def test_the_two_indexes_and_no_more(self):
        indexed = sorted(
            f.name
            for f in AICallRecord._meta.concrete_fields
            if getattr(f, "db_index", False) and not f.primary_key
        )
        self.assertEqual(indexed, ["created_at", "task_id"])
        self.assertEqual(AICallRecord._meta.indexes, [])

    def test_timestamps_are_timezone_aware(self):
        self.assertTrue(settings.USE_TZ)


class TheHooks(SimpleTestCase):
    def test_execute_graded_task_is_decorated_once_with_recorded_call(self):
        function = _function(_tree(SERVICES), "AIProcessor", "execute_graded_task")
        names = [ast.unparse(d) for d in function.decorator_list]
        self.assertEqual(names, ["call_record.recorded_call"])

    def test_the_four_notes_are_each_called_once_beside_their_line(self):
        function = _function(_tree(SERVICES), "AIProcessor", "execute_graded_task")
        statements = function.body

        def index_of(predicate):
            hits = [i for i, node in enumerate(statements) if predicate(node)]
            self.assertEqual(len(hits), 1, hits)
            return hits[0]

        def assigns(name):
            return lambda node: (
                isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == name for t in node.targets)
            )

        def note(name):
            return lambda node: (
                isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Call)
                and ast.unparse(node.value.func) == f"call_record.{name}"
            )

        self.assertEqual(
            index_of(note("note_estimate")), index_of(assigns("estimated_cost")) + 1
        )
        self.assertEqual(
            index_of(note("note_task_id")), index_of(assigns("task_id")) + 1
        )
        self.assertEqual(
            index_of(note("note_response")) + 1, index_of(assigns("resolved_course"))
        )
        # After the billed note come the cancellation check, then the
        # estimate (flattening the prompt moved into estimate_messages_cost).
        billed = index_of(note("note_billed"))
        self.assertEqual(billed + 2, index_of(assigns("estimated_cost")))
        between = statements[billed + 1]
        self.assertTrue(
            isinstance(between, ast.Expr)
            and isinstance(between.value, ast.Call)
            and ast.unparse(between.value.func) == "ensure_task_not_cancelled",
            ast.unparse(between),
        )

    def test_the_function_makes_no_other_call_into_the_module(self):
        function = _function(_tree(SERVICES), "AIProcessor", "execute_graded_task")
        used = {
            node.func.attr
            for node in ast.walk(function)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "call_record"
        }
        self.assertEqual(
            used, {"note_estimate", "note_task_id", "note_response", "note_billed"}
        )

    def test_the_hooks_are_five_lines_in_all(self):
        """One decorator line and four notes: the seam exception granted is
        about six lines, and this fails if it grows."""
        function = _function(_tree(SERVICES), "AIProcessor", "execute_graded_task")
        notes = [
            node
            for node in ast.walk(function)
            if isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Call)
            and ast.unparse(node.value.func).startswith("call_record.")
        ]
        self.assertEqual(len(notes) + len(function.decorator_list), 5)

    def test_the_provider_call_lines_are_not_edited(self):
        """__ai_model differs between beta and the epic line; neither call to
        it inside execute_graded_task may carry a hook argument."""
        function = _function(_tree(SERVICES), "AIProcessor", "execute_graded_task")
        for node in ast.walk(function):
            if isinstance(node, ast.Call) and ast.unparse(node.func).endswith(
                "__ai_model"
            ):
                self.assertNotIn("call_record", ast.unparse(node))

    def test_only_the_module_and_settings_name_the_switch(self):
        found = []
        for app in (
            "AutoGrader",
            "ai_processor",
            "assignments",
            "billing",
            "classrooms",
            "dashboard",
            "students",
            "users",
        ):
            for path in (BASE / app).rglob("*.py"):
                relative = path.relative_to(BASE).as_posix()
                if (
                    "/tests" in relative
                    or "/test_" in relative
                    or "/migrations/" in relative
                ):
                    continue
                if "AI_CALL_RECORD_ENABLED" in path.read_text(encoding="utf-8"):
                    found.append(relative)
        self.assertEqual(
            sorted(found), ["AutoGrader/settings.py", "ai_processor/call_record.py"]
        )


class TheCallSites(SimpleTestCase):
    def _sites(self):
        found = {}

        def visit(node, function):
            if isinstance(node, ast.FunctionDef):
                function = node.name
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "execute_graded_task"
            ):
                task_type = None
                for keyword in node.keywords:
                    if keyword.arg == "task_type" and isinstance(
                        keyword.value, ast.Constant
                    ):
                        task_type = keyword.value.value
                found[(function, task_type)] = found.get((function, task_type), 0) + 1
            for child in ast.iter_child_nodes(node):
                visit(child, function)

        visit(_tree(SERVICES), None)
        return found

    def test_every_call_site_is_listed_and_a_new_one_fails_here(self):
        self.assertEqual(set(self._sites()), set(CALL_SITES))

    def test_each_site_is_marked_exactly_when_its_task_type_proves_a_kind(self):
        for (function, task_type), marked in CALL_SITES.items():
            with self.subTest(function=function, task_type=task_type):
                self.assertEqual(task_type in call_record.KIND_BY_TASK_TYPE, marked)

    def test_a_kind_is_given_only_to_a_task_type_every_site_of_which_agrees(self):
        by_type = {}
        for function, task_type in CALL_SITES:
            by_type.setdefault(task_type, set()).add(function)
        for task_type in call_record.KIND_BY_TASK_TYPE:
            self.assertIn(task_type, by_type)
        # grade_assignment (a single call, a batch of a larger paper, the
        # summary) and extract_answer (single, part of a long paper, the
        # blank re-check) cannot be told apart by task type: no kind.
        self.assertNotIn("grade_assignment", call_record.KIND_BY_TASK_TYPE)
        self.assertNotIn("extract_answer", call_record.KIND_BY_TASK_TYPE)
        self.assertNotIn(None, call_record.KIND_BY_TASK_TYPE)

    def test_every_kind_is_in_the_models_closed_set(self):
        choices = {c[0] for c in (AICallRecord._meta.get_field("kind").choices or [])}
        self.assertTrue(set(call_record.KIND_BY_TASK_TYPE.values()) <= choices)

    def test_the_task_type_and_feature_sets_cover_every_listed_site(self):
        task_types = {
            c[0] for c in (AICallRecord._meta.get_field("task_type").choices or [])
        }
        for _function, task_type in CALL_SITES:
            if task_type is not None:
                self.assertIn(task_type, task_types)
        self.assertIn("other", task_types)


class NothingReadsOrExposesTheTable(SimpleTestCase):
    ALLOWED = {
        "ai_processor/models.py",
        "ai_processor/call_record.py",
    }

    def test_no_other_code_names_the_model(self):
        found = []
        for app in (
            "AutoGrader",
            "ai_processor",
            "assignments",
            "billing",
            "classrooms",
            "dashboard",
            "students",
            "users",
        ):
            for path in (BASE / app).rglob("*.py"):
                relative = path.relative_to(BASE).as_posix()
                if (
                    "/tests" in relative
                    or "/test_" in relative
                    or "/migrations/" in relative
                ):
                    continue
                if relative in self.ALLOWED:
                    continue
                if "AICallRecord" in path.read_text(encoding="utf-8"):
                    found.append(relative)
        self.assertEqual(found, [])

    def test_not_registered_in_the_admin(self):
        from django.contrib import admin

        self.assertNotIn(AICallRecord, admin.site._registry)

    def test_the_module_only_ever_creates_rows(self):
        text = (BASE / "ai_processor/call_record.py").read_text(encoding="utf-8")
        for forbidden in (
            ".save(",
            ".update(",
            ".delete(",
            "bulk_update",
            "select_for_update",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, text)

    def test_the_module_reads_no_table(self):
        text = (BASE / "ai_processor/call_record.py").read_text(encoding="utf-8")
        for forbidden in (
            ".objects.get",
            ".objects.filter",
            ".objects.all",
            ".first(",
            ".count(",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, text)

    def test_the_model_is_in_the_installed_app(self):
        self.assertIs(apps.get_model("ai_processor", "AICallRecord"), AICallRecord)


class TheMigration(SimpleTestCase):
    def test_it_is_one_create_model_and_nothing_else(self):
        loader = MigrationLoader(None, ignore_no_migrations=True)
        migration = loader.disk_migrations[("ai_processor", "0006_aicallrecord")]
        self.assertEqual(
            [type(op).__name__ for op in migration.operations], ["CreateModel"]
        )
        self.assertEqual(
            migration.dependencies,
            [("ai_processor", "0005_benchmarkrun_benchmarkquestionoutcome")],
        )

    def test_it_says_how_to_undo_it(self):
        text = (BASE / "ai_processor/migrations/0006_aicallrecord.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("To undo", text)


class TheMigrationGoesForwardBackAndForward(TransactionTestCase):
    TABLE = "ai_processor_aicallrecord"
    AT_0005 = [("ai_processor", "0005_benchmarkrun_benchmarkquestionoutcome")]
    AT_0006 = [("ai_processor", "0006_aicallrecord")]

    def _migrate(self, target):
        executor = MigrationExecutor(connection)
        others = [
            n for n in executor.loader.graph.leaf_nodes() if n[0] != "ai_processor"
        ]
        executor.migrate(list(target) + others)

    def _present(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass(%s)", [self.TABLE])
            return cursor.fetchone()[0] is not None

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(
            [n for n in executor.loader.graph.leaf_nodes() if n[0] == "ai_processor"]
        )
        super().tearDown()

    def test_back_drops_only_the_table_and_forward_makes_it_again(self):
        self.assertTrue(self._present())
        self._migrate(self.AT_0005)
        self.assertFalse(self._present())
        self._migrate(self.AT_0006)
        self.assertTrue(self._present())
