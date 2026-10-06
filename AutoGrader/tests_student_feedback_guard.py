"""
H-127: nothing in the repository hands a student the saved feedback column
or the teacher's review-queue fields.

A repository-wide guard, so it lives under AutoGrader/ with the others (a
slice that never runs the students app's tests still runs this one).

`StudentSubmission.feedback` (and the older `ai_feedback`) is the grading
result as the grader produced it; much of it is written for the teacher.
`formatted_grade` is the feedback formatter's output, whose prompt asks for
advice to the teacher too.
`needs_review`, `review_reasons`, `review_severity`, `review_tier` and
`grading_confidence` are the teacher's review queue; `review_reasons` holds
both AI graders' marks for each disputed question. Three student routes
returned one or the other as stored. The routes are fixed and tested in
students/tests_student_feedback_routes.py; this guard is for the next one.

Three rules, each over every production .py file (not test modules, not
migrations, not docs/ or scripts/):

1. READS. Every read of `.feedback`, `.ai_feedback` or `.formatted_grade`
   (an attribute, or `getattr(x, "feedback")`) is either inside a call to
   one of the three functions of students/feedback_projection.py, or in a
   function named in RAW_FEEDBACK_READERS with who it serves.
2. FIELD LISTS. Every class whose `Meta.fields` names one of those columns
   is named in SERIALIZERS, with who it serves; the two a student receives
   are checked for the code that hides the columns.
3. FILTERS. On the submissions list, every review-queue filter and
   ordering is one a student is refused.

A new read, serializer or filter fails here until someone decides who it
serves and writes that down.

What this guard does NOT show (the last four from Verifier 1's pre-read):
  * which caller reaches a teacher-shaped serializer. The route tests do,
    for the routes that exist. One student route, the answer upload,
    answers with the teacher's detail serializer; it is safe only because
    an upload is refused once the row is graded;
  * a column read through the ORM by name (`.values("feedback")`, a
    `feedback__...` lookup), or through a name built at run time;
  * a review-queue column read as an attribute into a hand-built
    dictionary or a method field: rule 1 covers the three feedback columns
    only, rules 2 and 3 cover the review fields in field lists and filters;
  * the list serializer built WITHOUT the request: its replacement of the
    review fields needs the caller, and with no request it replaces
    nothing;
  * whether a function named like a projection projects: a read inside a
    call of that name counts as projected, and
    `grading_result_for_formatter` keeps everything but one key (it is for
    the formatter, not for a student);
  * templates: an email template handed the whole submission object can
    print any column.
"""

import ast
import os

from django.conf import settings
from django.test import SimpleTestCase

#: The saved grading result, its older copy, and the formatter's output
#: (whose prompt fills it with advice to the teacher as well).
FEEDBACK_COLUMNS = {"feedback", "ai_feedback", "formatted_grade"}
REVIEW_QUEUE_FIELDS = {
    "needs_review",
    "review_reasons",
    "review_severity",
    "review_tier",
    "grading_confidence",
}
ALL_GUARDED = FEEDBACK_COLUMNS | REVIEW_QUEUE_FIELDS
#: The functions of students/feedback_projection.py. A read inside a call
#: to one of them never leaves as stored.
PROJECTIONS = {
    "student_safe_feedback",
    "student_safe_formatted_grade",
    "grading_result_for_formatter",
}
SKIPPED_DIRS = {"migrations", "node_modules", "venv", "docs", "scripts", "__pycache__"}

#: Rule 1. (file, function) -> who the raw read serves. None of these
#: returns the column to a student.
RAW_FEEDBACK_READERS = {
    (
        "students/serializers.py",
        "StudentSubmissionSerializer.update",
    ): "teacher: compares an edit with the AI's own feedback; returns nothing",
    (
        "students/serializers.py",
        "StudentSubmissionDetailSerializer.get_second_opinion",
    ): "teacher: the review screen's second-opinion block",
    (
        "students/serializers.py",
        "StudentSubmissionDetailSerializer.get_question_breakdown",
    ): "teacher: the review screen's per-question breakdown",
    (
        "students/serializers.py",
        "StudentSubmissionDetailSerializer.get_formatted_grade",
    ): "teacher branch only; a student caller gets "
    "student_safe_formatted_grade or nothing",
    (
        "students/views.py",
        "StudentSubmissionViewSet.teacher_feedback",
    ): "teacher route (IsTeacher): reads formatted_grade to see whether one "
    "exists; the feedback goes to the formatter prompt through "
    "grading_result_for_formatter",
    (
        "students/views.py",
        "StudentSubmissionViewSet.update_grade",
    ): "teacher route (IsTeacher): rewrites the summary numbers in place",
    (
        "assignments/tasks.py",
        "_reconcile_formatted_grade_numbers",
    ): "internal: reads max_points per question to correct the formatter",
    (
        "dashboard/services.py",
        "DashboardService.analyze_question_difficulty",
    ): "teacher dashboard: averages scores per question, returns no feedback",
    (
        "ai_processor/management/commands/grading_eval.py",
        "Command._collect",
    ): "operator command: counts, printed to the operator",
}

#: Rule 2. (file, class) -> who it serves.
TEACHER = "teacher"
STUDENT_PROJECTED = (
    "student: `feedback` through student_safe_feedback, `formatted_grade` "
    "through student_safe_formatted_grade"
)
STUDENT_REVIEW_HIDDEN = "both: review fields replaced for a student caller"
SERIALIZERS = {
    ("students/serializers.py", "StudentSubmissionSerializer"): TEACHER,
    ("students/serializers.py", "StudentSubmissionDetailSerializer"): TEACHER,
    (
        "students/serializers.py",
        "StudentSubmissionTeacherFeedbackSerializer",
    ): TEACHER,
    (
        "students/serializers.py",
        "StudentSubmissionDetailStudentVersionSerializer",
    ): STUDENT_PROJECTED,
    (
        "students/serializers.py",
        "StudentSubmissionListSerializer",
    ): STUDENT_REVIEW_HIDDEN,
}


def is_test_module(path):
    name = os.path.basename(path)
    return (
        name.startswith("tests")
        or name.startswith("test_")
        or name == "conftest.py"
        or "/tests/" in path.replace(os.sep, "/")
    )


def production_files(root):
    for directory, subdirectories, files in os.walk(root):
        subdirectories[:] = [
            d for d in subdirectories if d not in SKIPPED_DIRS and not d.startswith(".")
        ]
        for name in files:
            path = os.path.join(directory, name)
            relative = os.path.relpath(path, root).replace(os.sep, "/")
            if name.endswith(".py") and not is_test_module(relative):
                yield relative, path


def call_name(call):
    function = call.func
    if isinstance(function, ast.Name):
        return function.id
    return getattr(function, "attr", None)


def raw_feedback_reads(source):
    """(enclosing function, line) for every read of a feedback column that
    is not inside a call to one of the projections."""
    found = []

    def visit(node, stack, projected, called=False):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            stack = stack + [node.name]
        if isinstance(node, ast.Call):
            name = call_name(node)
            if name in PROJECTIONS:
                projected = True
            elif (
                name == "getattr"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value in FEEDBACK_COLUMNS
                and not projected
            ):
                found.append((".".join(stack) or "<module>", node.lineno))
        if (
            isinstance(node, ast.Attribute)
            and node.attr in FEEDBACK_COLUMNS
            and isinstance(node.ctx, ast.Load)
            and not projected
            # `ai_processor.formatted_grade(...)` calls a method of that
            # name; it reads no column.
            and not called
        ):
            found.append((".".join(stack) or "<module>", node.lineno))
        for child in ast.iter_child_nodes(node):
            visit(
                child,
                stack,
                projected,
                called=isinstance(node, ast.Call) and child is node.func,
            )

    visit(ast.parse(source), [], False)
    return found


def guarded_field_lists(source):
    """(class, the guarded columns its Meta.fields names)."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.ClassDef):
            continue
        for meta in node.body:
            if not (isinstance(meta, ast.ClassDef) and meta.name == "Meta"):
                continue
            for statement in meta.body:
                if isinstance(statement, ast.Assign) and any(
                    getattr(target, "id", None) == "fields"
                    for target in statement.targets
                ):
                    named = {
                        item.value
                        for item in ast.walk(statement.value)
                        if isinstance(item, ast.Constant)
                        and isinstance(item.value, str)
                    }
                    guarded = named & ALL_GUARDED
                    if guarded:
                        found.append((node.name, guarded))
    return found


class ScannerSelfTest(SimpleTestCase):
    """The scanner sees what it must, on source written for the purpose."""

    def test_a_raw_read_is_found_with_its_function(self):
        source = (
            "class S:\n"
            "    def get_summary(self, obj):\n"
            "        return obj.feedback or obj.ai_feedback\n"
        )
        self.assertEqual(
            raw_feedback_reads(source), [("S.get_summary", 3), ("S.get_summary", 3)]
        )

    def test_a_getattr_read_is_found(self):
        source = "def f(s):\n    return getattr(s, 'feedback', None)\n"
        self.assertEqual(raw_feedback_reads(source), [("f", 2)])

    def test_a_read_inside_a_projection_is_not_found(self):
        source = (
            "def f(s):\n"
            "    a = student_safe_feedback(s.feedback or s.ai_feedback)\n"
            "    b = module.grading_result_for_formatter(s.feedback)\n"
            "    return a, b\n"
        )
        self.assertEqual(raw_feedback_reads(source), [])

    def test_a_read_beside_a_projection_is_still_found(self):
        source = "def f(s):\n    return student_safe_feedback(s.feedback), s.feedback\n"
        self.assertEqual(raw_feedback_reads(source), [("f", 2)])

    def test_a_method_of_the_same_name_being_called_is_not_a_read(self):
        source = "def f(p, s):\n    return p.formatted_grade(s, 1)\n"
        self.assertEqual(raw_feedback_reads(source), [])

    def test_the_formatted_grade_column_is_a_guarded_read(self):
        source = "def f(s):\n    return s.formatted_grade\n"
        self.assertEqual(raw_feedback_reads(source), [("f", 2)])
        source = (
            "def f(s):\n    return student_safe_formatted_grade(s.formatted_grade)\n"
        )
        self.assertEqual(raw_feedback_reads(source), [])

    def test_a_write_is_not_a_read(self):
        source = "def f(s, g):\n    s.feedback = g\n"
        self.assertEqual(raw_feedback_reads(source), [])

    def test_a_field_list_naming_a_guarded_column_is_found(self):
        source = (
            "class A:\n"
            "    class Meta:\n"
            "        fields = ['id', 'feedback', 'review_reasons']\n"
            "class B:\n"
            "    class Meta:\n"
            "        fields = ['id', 'score']\n"
        )
        self.assertEqual(
            guarded_field_lists(source), [("A", {"feedback", "review_reasons"})]
        )


class StudentFeedbackGuardTest(SimpleTestCase):
    maxDiff = None
    reads: dict = {}
    field_lists: dict = {}
    files = 0

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.reads = {}
        cls.field_lists = {}
        cls.files = 0
        for relative, path in production_files(settings.BASE_DIR):
            cls.files += 1
            with open(path, encoding="utf-8") as handle:
                source = handle.read()
            if not any(name in source for name in ALL_GUARDED):
                continue  # nothing to parse for; most files
            for function, line in raw_feedback_reads(source):
                cls.reads.setdefault((relative, function), []).append(line)
            for name, guarded in guarded_field_lists(source):
                cls.field_lists[(relative, name)] = guarded

    def test_the_scan_covered_the_repository(self):
        """A scan of nothing would pass every rule below."""
        self.assertGreater(self.files, 150)
        for known in RAW_FEEDBACK_READERS:
            self.assertIn(known, self.reads, "a named reader no longer reads")
        for known in SERIALIZERS:
            self.assertIn(known, self.field_lists, "a named serializer is gone")

    def test_rule_1_every_raw_read_is_a_named_one(self):
        unnamed = {
            f"{path}:{lines[0]} in {function}"
            for (path, function), lines in self.reads.items()
            if (path, function) not in RAW_FEEDBACK_READERS
        }
        self.assertEqual(
            unnamed,
            set(),
            "the saved feedback column is read here and not through "
            "students/feedback_projection.py. If a student can receive the "
            "value, use student_safe_feedback; otherwise name the function "
            "in RAW_FEEDBACK_READERS with who it serves.",
        )

    def test_rule_2_every_field_list_with_a_guarded_column_is_a_named_one(self):
        unnamed = {
            f"{path}: {name} lists {sorted(guarded)}"
            for (path, name), guarded in self.field_lists.items()
            if (path, name) not in SERIALIZERS
        }
        self.assertEqual(
            unnamed,
            set(),
            "this serializer lists the feedback column or a review-queue "
            "field. Name it in SERIALIZERS with who it serves.",
        )

    def test_rule_2_the_student_detail_serializer_projects_both_columns(self):
        import inspect

        from students.serializers import (
            StudentSubmissionDetailStudentVersionSerializer as Serializer,
        )

        self.assertEqual(
            self.field_lists[
                (
                    "students/serializers.py",
                    "StudentSubmissionDetailStudentVersionSerializer",
                )
            ],
            {"feedback", "formatted_grade"},
            "the student's serializer lists a review-queue field",
        )
        for field, method, projection in (
            ("feedback", Serializer.get_feedback, "student_safe_feedback"),
            (
                "formatted_grade",
                Serializer.get_formatted_grade,
                "student_safe_formatted_grade",
            ),
        ):
            # A method field, not the model column...
            self.assertEqual(
                type(Serializer().fields[field]).__name__, "SerializerMethodField"
            )
            # ...and the method returns the projection (rule 1 already
            # holds that it holds no raw read).
            tree = ast.parse(inspect.getsource(method).lstrip())
            calls = {call_name(n) for n in ast.walk(tree) if isinstance(n, ast.Call)}
            self.assertIn(projection, calls)

    def test_rule_2_the_list_serializer_replaces_every_review_field(self):
        from students.serializers import StudentSubmissionListSerializer as Serializer

        listed = self.field_lists[
            ("students/serializers.py", "StudentSubmissionListSerializer")
        ]
        self.assertFalse(listed & FEEDBACK_COLUMNS)
        self.assertEqual(
            set(Serializer.STUDENT_REVIEW_FIELD_VALUES),
            listed & REVIEW_QUEUE_FIELDS,
            "the list serializer lists a review-queue field that a student "
            "caller is not sent a replacement for",
        )
        self.assertEqual(
            Serializer.STUDENT_REVIEW_FIELD_VALUES,
            {
                "needs_review": False,
                "review_reasons": None,
                "review_severity": None,
                "review_tier": None,
                "grading_confidence": None,
            },
        )

    def test_rule_3_a_student_is_refused_every_review_queue_filter(self):
        from students.views import StudentSubmissionViewSet as View

        review_filters = set(View.filterset_fields) & REVIEW_QUEUE_FIELDS
        review_orderings = set(View.ordering_fields) & REVIEW_QUEUE_FIELDS
        self.assertEqual(review_filters, set(View.TEACHER_ONLY_FILTERS))
        self.assertEqual(review_orderings, set(View.TEACHER_ONLY_ORDERINGS))
        # Not an empty agreement: the queue's filters are still there.
        self.assertEqual(review_filters, {"needs_review", "review_tier"})
        self.assertEqual(review_orderings, {"review_severity"})
        self.assertEqual(
            View.search_fields, ["student__first_name", "student__last_name"]
        )
