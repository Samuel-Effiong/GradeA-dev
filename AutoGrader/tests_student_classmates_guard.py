"""
H-147: nothing new hands a student a list of other students.

A repository-wide guard, so it lives under AutoGrader/ with the others.

The course answer gave a student the whole roster of their class, and the
teacher's assignment list with a count of classmates' work. The routes are
fixed and tested in classrooms/tests_student_sees_no_classmates.py; this
guard is for the next one.

Two rules:

1. ROSTER SERIALIZERS. Every place in a production .py file that names a
   serializer which lists students (`StudentSerializer`,
   `StudentListSerializer`), and every place in a `serializers.py` that
   names the teacher's assignment list (`AssignmentListSerializer`, which
   carries `submission_count`), is named in ROSTER_SITES with who it
   serves. The two sites in the course answer are also checked for the
   student's own branch coming first.
2. ACTIONS. Every action of the course, enrolment and session views is
   named in ACTIONS with whether a student can call it and what they are
   sent. For the ones a student cannot call, the permission that refuses
   them is checked where it is declared on the action or the view.

A new site or action fails here until someone decides who it serves and
writes that down.

What this guard does NOT show:
  * a roster built by hand (a dictionary or a list comprehension over
    enrolments) instead of with one of the named serializers;
  * a new serializer that lists users under another name;
  * views outside classrooms/views.py: the submissions, assignments and
    dashboard views are not classified here (H-141 is to classify the
    submissions view);
  * that a permission named here refuses what its name says: the route
    tests do that, for the routes that have them;
  * what a queryset returns: "own rows only" in ACTIONS is a reading of
    the code, written down, not something this module executes.
"""

import ast
import os

from django.conf import settings
from django.test import SimpleTestCase

LISTS_STUDENTS = {"StudentSerializer", "StudentListSerializer"}
STAFF_ASSIGNMENT_LIST = "AssignmentListSerializer"
SKIPPED_DIRS = {
    "migrations",
    "node_modules",
    "venv",
    ".venv",
    "docs",
    "scripts",
    "__pycache__",
    ".git",
}

#: (file, enclosing class.function, serializer) -> who it serves.
ROSTER_SITES = {
    (
        "classrooms/serializers.py",
        "CourseSerializer.get_students",
        "StudentSerializer",
    ): "staff: the roster. A student is answered by _own_entry first.",
    (
        "classrooms/serializers.py",
        "CourseSerializer._own_entry",
        "StudentSerializer",
    ): "student: their own entry, built from their own enrolment only.",
    (
        "classrooms/serializers.py",
        "CourseSerializer.get_assignments",
        "AssignmentListSerializer",
    ): "staff. A student is answered with AssignmentListStudentSerializer first.",
    (
        "classrooms/views.py",
        "StudentCourseViewSet.get_serializer_class",
        "StudentListSerializer",
    ): "staff: my_students only, which is IsTeacher.",
    (
        "classrooms/views.py",
        "StudentCourseViewSet.my_students",
        "StudentListSerializer",
    ): "staff: the action is IsTeacher.",
}

#: Why a student cannot call an action. Each is checked in the code.
TEACHER_WRITE = "unsafe method under IsTeacherOrReadOnly"
IS_TEACHER = "IsTeacher on the action"
NO_SUCH_METHOD = "the view does not accept the method"
SESSION_WRITE = "unsafe method under CanManageSession"

#: (view, action) -> (can a student call it, what they are sent or why not).
ACTIONS = {
    ("CourseViewSet", "list"): (True, "their own courses; H-147's answer"),
    ("CourseViewSet", "retrieve"): (True, "a course of theirs; H-147's answer"),
    ("CourseViewSet", "my_courses"): (True, "their own courses; H-147's answer"),
    ("CourseViewSet", "create"): (False, TEACHER_WRITE),
    ("CourseViewSet", "update"): (False, NO_SUCH_METHOD),
    ("CourseViewSet", "partial_update"): (False, TEACHER_WRITE),
    ("CourseViewSet", "destroy"): (False, TEACHER_WRITE),
    ("CourseViewSet", "students"): (False, TEACHER_WRITE),
    ("CourseViewSet", "remove_student"): (False, TEACHER_WRITE),
    ("CourseViewSet", "create_topics"): (False, TEACHER_WRITE),
    ("CourseViewSet", "direct_add_student"): (False, IS_TEACHER),
    ("CourseViewSet", "bulk_add_students"): (False, IS_TEACHER),
    ("CourseViewSet", "student_summary"): (False, IS_TEACHER),
    ("CourseViewSet", "handle_expired_token"): (
        True,
        "anyone, signed in or not: renews an activation link; sends no roster",
    ),
    ("StudentCourseViewSet", "list"): (True, "their own enrolments only"),
    ("StudentCourseViewSet", "retrieve"): (True, "an enrolment of their own"),
    ("StudentCourseViewSet", "create"): (False, NO_SUCH_METHOD),
    ("StudentCourseViewSet", "update"): (False, NO_SUCH_METHOD),
    ("StudentCourseViewSet", "partial_update"): (False, TEACHER_WRITE),
    ("StudentCourseViewSet", "destroy"): (False, TEACHER_WRITE),
    ("StudentCourseViewSet", "my_students"): (False, IS_TEACHER),
    ("SessionViewSet", "list"): (True, "sessions of their courses; no staff ids"),
    ("SessionViewSet", "retrieve"): (True, "a session of theirs; no staff ids"),
    ("SessionViewSet", "create"): (False, SESSION_WRITE),
    ("SessionViewSet", "update"): (False, NO_SUCH_METHOD),
    ("SessionViewSet", "partial_update"): (False, SESSION_WRITE),
    ("SessionViewSet", "destroy"): (False, SESSION_WRITE),
}
STANDARD_ACTIONS = {
    "list": "get",
    "retrieve": "get",
    "create": "post",
    "update": "put",
    "partial_update": "patch",
    "destroy": "delete",
}
SAFE = {"get", "head", "options"}


def production_files():
    root = str(settings.BASE_DIR)
    for directory, subdirs, names in os.walk(root):
        subdirs[:] = [
            d for d in subdirs if d not in SKIPPED_DIRS and not d.startswith(".")
        ]
        for name in names:
            if not name.endswith(".py"):
                continue
            if name.startswith(("tests", "test_")) or name == "conftest.py":
                continue
            path = os.path.join(directory, name)
            yield os.path.relpath(path, root).replace(os.sep, "/"), path


def named_sites(tree, names):
    """(enclosing class.function, name, line, in a decorator) for every use
    of one of `names` as a name in the code (not an import, not a class
    definition, not a comment)."""
    found = []

    def visit(node, scope, in_decorator):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            inner = scope + [node.name]
            for decorator in node.decorator_list:
                visit(decorator, inner, True)
            for field, value in ast.iter_fields(node):
                if field == "decorator_list":
                    continue
                for child in value if isinstance(value, list) else [value]:
                    if isinstance(child, ast.AST):
                        visit(child, inner, in_decorator)
            return
        if isinstance(node, ast.Name) and node.id in names:
            found.append((".".join(scope), node.id, node.lineno, in_decorator))
        for child in ast.iter_child_nodes(node):
            visit(child, scope, in_decorator)

    visit(tree, [], False)
    return found


def roster_sites_in(relpath, source):
    names = set(LISTS_STUDENTS)
    if os.path.basename(relpath) == "serializers.py":
        names.add(STAFF_ASSIGNMENT_LIST)
    return [
        (relpath, scope, name, line, in_decorator)
        for scope, name, line, in_decorator in named_sites(ast.parse(source), names)
    ]


def student_branch_comes_first(source, class_name, function_name, staff_name, marker):
    """True when, in class.function, a `return` whose expression names
    `marker` sits inside an `if` and above the first use of `staff_name`
    in the body."""
    tree = ast.parse(source)
    for cls in ast.walk(tree):
        if not (isinstance(cls, ast.ClassDef) and cls.name == class_name):
            continue
        for function in cls.body:
            if not (
                isinstance(function, ast.FunctionDef) and function.name == function_name
            ):
                continue
            staff_lines = [
                node.lineno
                for statement in function.body
                for node in ast.walk(statement)
                if isinstance(node, ast.Name) and node.id == staff_name
            ]
            student_returns = [
                ret.lineno
                for statement in function.body
                if isinstance(statement, ast.If)
                for ret in ast.walk(statement)
                if isinstance(ret, ast.Return)
                and ret.value is not None
                and any(
                    (isinstance(n, ast.Name) and n.id == marker)
                    or (isinstance(n, ast.Attribute) and n.attr == marker)
                    for n in ast.walk(ret.value)
                )
            ]
            return bool(
                staff_lines
                and student_returns
                and min(student_returns) < min(staff_lines)
            )
    return False


class StudentClassmatesGuardTest(SimpleTestCase):
    maxDiff = None

    # ---- rule 1

    def found_sites(self):
        found = {}
        for relpath, path in production_files():
            with open(path, encoding="utf-8") as handle:
                source = handle.read()
            if not any(
                name in source for name in LISTS_STUDENTS | {STAFF_ASSIGNMENT_LIST}
            ):
                continue
            for site_path, scope, name, line, _ in roster_sites_in(relpath, source):
                found.setdefault((site_path, scope, name), []).append(line)
        return found

    def test_rule_1_every_roster_site_is_classified(self):
        found = self.found_sites()
        # The scan really reads the code: the course answer's sites are seen.
        self.assertIn(
            (
                "classrooms/serializers.py",
                "CourseSerializer._own_entry",
                "StudentSerializer",
            ),
            found,
        )
        unclassified = {
            site: lines for site, lines in found.items() if site not in ROSTER_SITES
        }
        self.assertEqual(
            unclassified,
            {},
            "A serializer that lists students (or the teacher's assignment "
            "list, in a serializers.py) is named here without a decision on "
            "who it serves. A student must be sent nothing of their "
            "classmates (H-147). Add the site to ROSTER_SITES with its reason.",
        )

    def test_rule_1_no_classified_site_has_gone(self):
        self.assertEqual(sorted(set(ROSTER_SITES) - set(self.found_sites())), [])

    def test_rule_1_the_course_answer_serves_the_student_first(self):
        path = os.path.join(str(settings.BASE_DIR), "classrooms", "serializers.py")
        with open(path, encoding="utf-8") as handle:
            source = handle.read()
        self.assertTrue(
            student_branch_comes_first(
                source,
                "CourseSerializer",
                "get_students",
                "StudentSerializer",
                "_own_entry",
            ),
            "CourseSerializer.get_students must return the student's own "
            "entry before it builds the roster.",
        )
        self.assertTrue(
            student_branch_comes_first(
                source,
                "CourseSerializer",
                "get_assignments",
                "AssignmentListSerializer",
                "AssignmentListStudentSerializer",
            ),
            "CourseSerializer.get_assignments must return the student's own "
            "view before it builds the teacher's list.",
        )

    def test_rule_1_the_scan_sees_a_site_when_there_is_one(self):
        """The scan on a made-up file: a use in a method, in a decorator
        and as a bare name are all seen; an import and a comment are not;
        the teacher's assignment list counts only in a serializers.py."""
        source = (
            "from students.serializers import StudentSerializer\n"
            "# StudentSerializer in a comment\n"
            "class A:\n"
            "    @schema(StudentListSerializer(many=True))\n"
            "    def roster(self):\n"
            "        return StudentSerializer(self.all(), many=True).data\n"
            "    def which(self):\n"
            "        return AssignmentListSerializer\n"
        )
        seen = {
            (scope, name)
            for _, scope, name, _, _ in roster_sites_in("x/views.py", source)
        }
        self.assertEqual(
            seen,
            {("A.roster", "StudentListSerializer"), ("A.roster", "StudentSerializer")},
        )
        seen = {
            (scope, name)
            for _, scope, name, _, _ in roster_sites_in("x/serializers.py", source)
        }
        self.assertIn(("A.which", "AssignmentListSerializer"), seen)

    def test_rule_1_the_order_check_fails_when_the_roster_comes_first(self):
        good = (
            "class C:\n"
            "    def get_students(self, obj):\n"
            "        if viewer is not None:\n"
            "            return self._own_entry(obj, viewer)\n"
            "        return StudentSerializer(everyone, many=True).data\n"
        )
        bad_order = (
            "class C:\n"
            "    def get_students(self, obj):\n"
            "        data = StudentSerializer(everyone, many=True).data\n"
            "        if viewer is not None:\n"
            "            return self._own_entry(obj, viewer)\n"
            "        return data\n"
        )
        no_branch = (
            "class C:\n"
            "    def get_students(self, obj):\n"
            "        return StudentSerializer(everyone, many=True).data\n"
        )
        args = ("C", "get_students", "StudentSerializer", "_own_entry")
        self.assertTrue(student_branch_comes_first(good, *args))
        self.assertFalse(student_branch_comes_first(bad_order, *args))
        self.assertFalse(student_branch_comes_first(no_branch, *args))

    # ---- rule 2

    def views(self):
        from classrooms.views import CourseViewSet, SessionViewSet, StudentCourseViewSet

        return {
            view.__name__: view
            for view in (CourseViewSet, StudentCourseViewSet, SessionViewSet)
        }

    def test_rule_2_every_action_is_classified(self):
        actual = set()
        for name, view in self.views().items():
            actual |= {(name, action) for action in STANDARD_ACTIONS}
            actual |= {(name, extra.__name__) for extra in view.get_extra_actions()}
        self.assertGreater(len(actual), 20)
        self.assertEqual(
            sorted(actual - set(ACTIONS)),
            [],
            "An action of the course, enrolment or session view has no "
            "decision on whether a student can call it and what they are "
            "sent (H-147). Add it to ACTIONS.",
        )
        self.assertEqual(sorted(set(ACTIONS) - actual), [])

    def test_rule_2_each_refusal_is_where_the_table_says(self):
        from classrooms.permissions import (
            CanManageSession,
            IsTeacher,
            IsTeacherOrReadOnly,
        )

        views = self.views()
        checked = 0
        for (view_name, action), (reachable, why) in sorted(ACTIONS.items()):
            if reachable:
                continue
            view = views[view_name]
            extras = {extra.__name__: extra for extra in view.get_extra_actions()}
            if action in extras:
                methods = set(extras[action].mapping)
                declared = extras[action].kwargs.get("permission_classes")
            else:
                methods = {STANDARD_ACTIONS[action]}
                declared = None
            effective = list(declared or view.permission_classes)
            label = f"{view_name}.{action}"
            if why == NO_SUCH_METHOD:
                self.assertFalse(methods & set(view.http_method_names), label)
            elif why == IS_TEACHER:
                self.assertIn(IsTeacher, effective, label)
            elif why == TEACHER_WRITE:
                self.assertIn(IsTeacherOrReadOnly, effective, label)
                self.assertFalse(methods & SAFE, label)
            elif why == SESSION_WRITE:
                self.assertIn(CanManageSession, effective, label)
                self.assertFalse(methods & SAFE, label)
            else:
                self.fail(f"{label}: no check is written for {why!r}")
            checked += 1
        self.assertGreater(checked, 15)
