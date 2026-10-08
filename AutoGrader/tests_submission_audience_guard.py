"""
H-141: no action a student can call answers with a staff serializer of a
submission.

A repository-wide guard, so it lives under AutoGrader/ with the others.

H-127's guard reads which serializers name a guarded column; it cannot
tell which caller reaches one. The student's own upload route answered
with the staff detail serializer, built without the request, and nothing
failed. This guard is for that shape, on the submissions view
(students/views.py, StudentSubmissionViewSet):

1. AUDIENCE. Every serializer of StudentSubmission in
   students/serializers.py says who it is for: `audience = "staff"`,
   `"student"` or `"both"`. "both" also names, in `student_answer_in`, the
   methods that change its answer for a student, and each must exist.
2. ACTIONS. Every action of the view is named in ACTIONS with who can call
   it and which submission serializers its own source builds. Who can call
   it is checked against the view's `get_permissions`; the serializers
   against the source. A new action, or a new serializer in an old one,
   fails here until someone classifies it.
3. THE RULE. In an action a student can call:
     * a "staff" serializer is built only in the `else` of a test for a
       student caller;
     * every serializer built is given a `context=`, which carries the
       request: "both" and "student" serializers decide by the caller.
4. The two actions served through `get_serializer` (list, retrieve) give a
   student caller a serializer that is not "staff".

What this guard does NOT show:
  * other views (assignments, classrooms, dashboard): only the one named;
  * a response built by hand as a dictionary, or a serializer built inside
    a helper function the action calls: only the action's own source is
    read;
  * that a "both" or "student" serializer hides what it should: H-127's and
    H-133's tests do, per field;
  * that the `context=` given holds the request: it sees the keyword.
"""

import ast
import inspect
import textwrap

from django.test import SimpleTestCase
from rest_framework import serializers
from rest_framework.test import APIRequestFactory

import students.serializers
from classrooms.permissions import IsTeacher
from students.models import StudentSubmission
from students.views import StudentSubmissionViewSet
from users.models import UserTypes

STAFF = "staff"
STUDENT = "student"
BOTH = "both"
AUDIENCES = {STAFF, STUDENT, BOTH}

#: Who can call an action.
A_STUDENT_CAN = "a student can call it"
TEACHER_ONLY = "teacher only (IsTeacher)"

#: Rule 2. action -> (who can call it, the submission serializers its own
#: source builds). Read from students/views.py on 2026-10-07.
ACTIONS = {
    # Served through get_serializer; see rule 4.
    "list": (A_STUDENT_CAN, set()),
    "retrieve": (
        A_STUDENT_CAN,
        {
            "StudentSubmissionDetailStudentVersionSerializer",
            "StudentSubmissionDetailSerializer",
        },
    ),
    # Refuses everything: "Student can only upload answers".
    "create": (A_STUDENT_CAN, set()),
    "upload_answers": (A_STUDENT_CAN, {"StudentUploadAnswerSerializer"}),
    # The queued twins answer with task ids, not a submission.
    "upload_answers_async": (A_STUDENT_CAN, set()),
    "partial_update": (A_STUDENT_CAN, {"StudentSubmissionListSerializer"}),
    "update_async": (A_STUDENT_CAN, set()),
    "grade": (TEACHER_ONLY, {"StudentSubmissionDetailSerializer"}),
    "grade_async": (TEACHER_ONLY, set()),
    "schedule_grade_async": (TEACHER_ONLY, set()),
    "teacher_feedback": (TEACHER_ONLY, {"StudentSubmissionTeacherFeedbackSerializer"}),
    "update_grade": (
        TEACHER_ONLY,
        {"StudentSubmissionGradeUpdateSerializer", "StudentSubmissionDetailSerializer"},
    ),
    "batch_upload": (TEACHER_ONLY, set()),
    "publish_grade": (TEACHER_ONLY, {"StudentSubmissionDetailSerializer"}),
    "mark_reviewed": (TEACHER_ONLY, {"StudentSubmissionDetailSerializer"}),
    "destroy": (TEACHER_ONLY, set()),
}

#: The actions ModelViewSet brings that are routed here. `update` (PUT) is
#: not: the view's http_method_names has no "put", which a test holds.
INHERITED_ACTIONS = {"list", "destroy"}


def submission_serializers():
    """name -> class, for every ModelSerializer of StudentSubmission that
    students/serializers.py defines."""
    found = {}
    for name, value in vars(students.serializers).items():
        if not (
            inspect.isclass(value)
            and issubclass(value, serializers.ModelSerializer)
            and value.__module__ == students.serializers.__name__
        ):
            continue
        if getattr(getattr(value, "Meta", None), "model", None) is StudentSubmission:
            found[name] = value
    return found


def is_a_test_for_a_student(test):
    """`<something>.user_type == UserTypes.STUDENT` (or "STUDENT")."""
    if not (isinstance(test, ast.Compare) and len(test.ops) == 1):
        return False
    if not isinstance(test.ops[0], ast.Eq):
        return False
    left, right = test.left, test.comparators[0]
    names_the_type = isinstance(left, ast.Attribute) and left.attr == "user_type"
    names_a_student = (
        isinstance(right, ast.Attribute) and right.attr == "STUDENT"
    ) or (isinstance(right, ast.Constant) and right.value == "STUDENT")
    return names_the_type and names_a_student


def serializers_built(source, names):
    """For every call of one of `names` in a function's source:
    (name, given a context= keyword, inside the else of a test for a
    student caller)."""
    found = []

    def visit(node, in_staff_branch):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", None)
            if name in names:
                has_context = any(k.arg == "context" for k in node.keywords)
                found.append((name, has_context, in_staff_branch))
        if isinstance(node, ast.If) and is_a_test_for_a_student(node.test):
            visit(node.test, in_staff_branch)
            for child in node.body:
                visit(child, in_staff_branch)
            for child in node.orelse:
                visit(child, True)
            return
        for inner in ast.iter_child_nodes(node):
            visit(inner, in_staff_branch)

    visit(ast.parse(textwrap.dedent(source)), False)
    return found


def actions_of_the_view():
    """The view's own actions (decorated with @action, or a standard
    action it defines) and the inherited ones that are routed."""
    standard = {"list", "retrieve", "create", "partial_update", "destroy"}
    names = set(INHERITED_ACTIONS)
    for name, value in vars(StudentSubmissionViewSet).items():
        if not inspect.isfunction(value):
            continue
        if hasattr(value, "mapping") or name in standard:
            names.add(name)
    return names


def who_can_call(action):
    view = StudentSubmissionViewSet()
    view.action = action
    teacher_only = any(
        isinstance(permission, IsTeacher) for permission in view.get_permissions()
    )
    return TEACHER_ONLY if teacher_only else A_STUDENT_CAN


class ScannerSelfTest(SimpleTestCase):
    """The scanner sees what it must, on source written for the purpose."""

    NAMES = {"Staff", "Both"}

    def built(self, source):
        return serializers_built(source, self.NAMES)

    def test_a_serializer_built_without_a_context_is_seen(self):
        self.assertEqual(
            self.built("def f(self):\n    return Staff(row).data\n"),
            [("Staff", False, False)],
        )

    def test_a_context_keyword_is_seen(self):
        self.assertEqual(
            self.built("def f(self):\n    return Both(row, context=c).data\n"),
            [("Both", True, False)],
        )

    def test_the_else_of_a_test_for_a_student_is_the_staff_branch(self):
        source = (
            "def f(self, request):\n"
            "    if request.user.user_type == UserTypes.STUDENT:\n"
            "        s = Both(row, context=c)\n"
            "    else:\n"
            "        s = Staff(row, context=c)\n"
        )
        self.assertEqual(
            self.built(source), [("Both", True, False), ("Staff", True, True)]
        )

    def test_the_body_of_that_test_is_not_the_staff_branch(self):
        source = (
            "def f(self, request):\n"
            "    if request.user.user_type == 'STUDENT':\n"
            "        s = Staff(row)\n"
        )
        self.assertEqual(self.built(source), [("Staff", False, False)])

    def test_the_else_of_another_test_is_not_the_staff_branch(self):
        source = (
            "def f(self, request):\n"
            "    if request.user.is_active:\n"
            "        pass\n"
            "    else:\n"
            "        s = Staff(row)\n"
        )
        self.assertEqual(self.built(source), [("Staff", False, False)])

    def test_a_test_for_a_teacher_does_not_count(self):
        source = (
            "def f(self, request):\n"
            "    if request.user.user_type == UserTypes.TEACHER:\n"
            "        pass\n"
            "    else:\n"
            "        s = Staff(row)\n"
        )
        self.assertEqual(self.built(source), [("Staff", False, False)])


class SubmissionAudienceGuardTest(SimpleTestCase):
    def audience(self, name):
        return getattr(submission_serializers()[name], "audience", None)

    def source_of(self, action):
        return inspect.getsource(getattr(StudentSubmissionViewSet, action))

    def test_the_census_found_the_serializers(self):
        found = submission_serializers()
        self.assertGreaterEqual(len(found), 7, sorted(found))
        self.assertIn("StudentSubmissionDetailSerializer", found)

    def test_rule_1_every_serializer_of_a_submission_says_who_it_is_for(self):
        undeclared = sorted(
            name
            for name, value in submission_serializers().items()
            if value.__dict__.get("audience") not in AUDIENCES
        )
        self.assertEqual(
            undeclared,
            [],
            "A serializer of StudentSubmission must declare, in its own "
            'body, audience = "staff", "student" or "both" (H-141).',
        )

    def test_rule_1_a_serializer_for_both_names_where_a_student_is_answered(self):
        for name, value in submission_serializers().items():
            if value.__dict__.get("audience") != BOTH:
                continue
            with self.subTest(serializer=name):
                methods = value.__dict__.get("student_answer_in")
                self.assertTrue(methods, "student_answer_in is missing or empty")
                for method in methods:
                    self.assertIn(method, value.__dict__)
                    self.assertIn("STUDENT", inspect.getsource(value.__dict__[method]))

    def test_rule_1_the_serializers_are_for_whom_this_guard_was_told(self):
        """Changing an audience is a decision; it is made here too."""
        self.assertEqual(
            {name: self.audience(name) for name in submission_serializers()},
            {
                "StudentSubmissionSerializer": STAFF,
                "StudentSubmissionUpdateSerializer": STAFF,
                "StudentSubmissionListSerializer": BOTH,
                "StudentSubmissionDetailSerializer": STAFF,
                "StudentSubmissionDetailStudentVersionSerializer": STUDENT,
                "StudentUploadAnswerSerializer": STUDENT,
                "StudentSubmissionGradeUpdateSerializer": STAFF,
                "StudentSubmissionTeacherFeedbackSerializer": STAFF,
            },
        )

    def test_rule_2_every_action_of_the_view_is_named(self):
        self.assertEqual(sorted(actions_of_the_view()), sorted(ACTIONS))

    def test_rule_2_put_is_not_routed(self):
        self.assertNotIn("put", StudentSubmissionViewSet.http_method_names)

    def test_rule_2_who_can_call_each_action_is_as_named(self):
        for action, (who, _) in ACTIONS.items():
            with self.subTest(action=action):
                self.assertEqual(who_can_call(action), who)

    def test_rule_2_each_action_builds_the_serializers_named_and_no_other(self):
        names = set(submission_serializers())
        for action, (_, expected) in ACTIONS.items():
            if action in INHERITED_ACTIONS:
                continue
            with self.subTest(action=action):
                built = {
                    name
                    for name, _, _ in serializers_built(self.source_of(action), names)
                }
                self.assertEqual(built, expected)

    def test_rule_3_a_student_action_builds_staff_only_in_the_staff_branch(self):
        names = set(submission_serializers())
        for action, (who, _) in ACTIONS.items():
            if who != A_STUDENT_CAN or action in INHERITED_ACTIONS:
                continue
            for name, _, in_staff_branch in serializers_built(
                self.source_of(action), names
            ):
                with self.subTest(action=action, serializer=name):
                    if self.audience(name) == STAFF:
                        self.assertTrue(
                            in_staff_branch,
                            f"{action} can be called by a student and builds "
                            f"the staff serializer {name} outside the else "
                            "of a test for a student caller (H-141).",
                        )

    def test_rule_3_a_student_action_gives_every_serializer_the_context(self):
        names = set(submission_serializers())
        for action, (who, _) in ACTIONS.items():
            if who != A_STUDENT_CAN or action in INHERITED_ACTIONS:
                continue
            for name, has_context, _ in serializers_built(
                self.source_of(action), names
            ):
                with self.subTest(action=action, serializer=name):
                    self.assertTrue(
                        has_context,
                        f"{action} builds {name} without context=: the "
                        "serializer cannot know a student is asking (H-141).",
                    )

    def test_rule_4_get_serializer_gives_a_student_no_staff_serializer(self):
        request = APIRequestFactory().get("/")
        request.user = type("Student", (), {"user_type": UserTypes.STUDENT})()
        for action in ("list", "retrieve"):
            with self.subTest(action=action):
                view = StudentSubmissionViewSet()
                view.action = action
                view.request = request  # type: ignore[assignment]
                chosen = view.get_serializer_class()
                self.assertIn(getattr(chosen, "audience", None), {STUDENT, BOTH})
