"""H-38 sweep guard: teacher access to a course goes through one rule.

`course.teacher == user` never stops being true, so a teacher removed from a
school kept reading and writing that school's courses everywhere a view
scoped on it directly. The rule now lives in `classrooms.models`
(`teacher_course_access_q`, `teacher_can_reach_course`, `reachable_courses`).

This test fails when a non-test module scopes on the owner directly again.
A line that legitimately does so (reporting, an INDIVIDUAL-session-only
lookup, display) must be listed below WITH its reason; the list is checked
for entries that no longer match, so it cannot rot.
"""

import os
import re
from pathlib import Path

from django.test import SimpleTestCase, TestCase

from classrooms.models import (
    Course,
    School,
    Session,
    SessionOwnerType,
    reachable_courses,
    teacher_can_reach_course,
    teacher_course_access_q,
)
from users.models import CustomUser, UserTypes

ROOT = Path(__file__).resolve().parent.parent

# Direct owner scoping: a filter kwarg on the course's teacher, a Course
# lookup by teacher, or a comparison of the course's teacher to the user.
PATTERNS = [
    re.compile(r"course__teacher(_id)?\s*=[^=]"),
    re.compile(r"Course\.objects\.(filter|get)\([^)]*\bteacher="),
    re.compile(r"get_object_or_404\(\s*Course\b[^)]*\bteacher="),
    re.compile(r"course\.teacher(_id)?\s*(!=|==)\s*(self\.)?(request\.)?user"),
    re.compile(r"(self\.)?request\.user\s*(!=|==)\s*[\w.]*course\.teacher"),
    # Owner-id comparisons against the user, on any object. The two allowed
    # non-course uses (a Session's owner) are listed below.
    re.compile(r"teacher_id\s*(!=|==)\s*[\w.]*user\.(id|pk)\b"),
    re.compile(r"[\w.]*user\.(id|pk)\s*(!=|==)\s*[\w.]*teacher_id\b"),
]

SKIP_DIRS = {"tests", "migrations", "docs", "scripts", "team", "node_modules"}
# The rule itself.
HELPER_FILE = "classrooms/models.py"

# (file, stripped line) -> why direct scoping is correct there.
ALLOWED = {
    (
        "classrooms/signals.py",
        "if Course.objects.filter(teacher=teacher).count() != 1:",
    ): "system signal counting the teacher's own courses; not an access decision",
    (
        "classrooms/views.py",
        'Assignment.objects.filter(course__teacher=OuterRef("id"))',
    ): "admin reporting aggregate per teacher; not teacher access",
    (
        "classrooms/views.py",
        'StudentCourse.objects.filter(course__teacher=OuterRef("id"))',
    ): "admin reporting aggregate per teacher; not teacher access",
    (
        "classrooms/services/roster_import.py",
        "enrollments__course__teacher=course.teacher,",
    ): "name match limited to students already with this course's teacher; the "
    "caller's access to `course` was scoped before this runs",
    (
        "classrooms/serializers.py",
        "if user.is_under_license() or value.teacher_id != user.id:",
    ): "ownership of an INDIVIDUAL Session (value is a Session, not a Course)",
    (
        "classrooms/permissions.py",
        "return obj.teacher_id == user.id",
    ): "CanManageSession object permission; obj is an INDIVIDUAL Session",
    (
        "students/serializers.py",
        "if enrollment.course.teacher_id == request.user.id:",
    ): "display fallback choosing which course to show; the enrollments were "
    "already scoped by the queryset",
    (
        "dashboard/views.py",
        "course__teacher=teacher, course__session=session",
    ): "session was fetched with teacher=teacher, so it is an INDIVIDUAL "
    "session (SCHOOL sessions have no teacher)",
    (
        "dashboard/views.py",
        "Course.objects.filter(teacher=teacher, session=session)",
    ): "same INDIVIDUAL-session lookup as above",
    (
        "dashboard/views.py",
        "course__teacher=teacher,",
    ): "same INDIVIDUAL-session lookup as above",
}


def scan_source(text):
    """Stripped lines of `text` that scope on a course's owner directly."""
    hits = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if any(p.search(stripped) for p in PATTERNS):
            hits.append(stripped)
    return hits


def scan_repository():
    found = set()
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [
            d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")
        ]
        for name in filenames:
            if not name.endswith(".py"):
                continue
            if name.startswith(("test_", "tests_")) or name == "tests.py":
                continue
            path = Path(dirpath) / name
            rel = path.relative_to(ROOT).as_posix()
            if rel == HELPER_FILE:
                continue
            for line in scan_source(path.read_text(encoding="utf-8")):
                found.add((rel, line))
    return found


class TeacherAccessSweepTests(SimpleTestCase):
    def test_no_unlisted_direct_owner_scoping(self):
        unlisted = sorted(scan_repository() - set(ALLOWED))
        self.assertEqual(
            unlisted,
            [],
            "These lines scope on a course's owner directly, which keeps "
            "working for a teacher removed from the school (H-38). Use "
            "classrooms.models.teacher_course_access_q / reachable_courses "
            "/ teacher_can_reach_course, or add the line to ALLOWED with the "
            "reason it is safe.",
        )

    def test_allowlist_has_no_stale_entries(self):
        stale = sorted(set(ALLOWED) - scan_repository())
        self.assertEqual(stale, [], "Remove these from ALLOWED; they no longer match.")

    def test_scanner_flags_every_shape_it_exists_to_catch(self):
        for bad in (
            "qs.filter(course__teacher=user)",
            "Course.objects.filter(teacher=request.user)",
            "Course.objects.get(id=course_id, teacher=user)",
            "get_object_or_404(Course, id=course_id, teacher=request.user)",
            "if assignment.course.teacher != request.user:",
            "if request.user != course.teacher:",
            "if value.teacher_id != user.id:",
            "Topic.objects.filter(assignment__course__teacher=teacher)",
        ):
            with self.subTest(bad=bad):
                self.assertEqual(scan_source(bad), [bad])

    def test_scanner_ignores_the_shapes_it_must_not_flag(self):
        for fine in (
            "# course__teacher=user is what H-38 removed",
            'values("course__teacher")',
            "reachable_courses(user).get(id=course_id)",
            "teacher_course_access_q(user, prefix='course__')",
        ):
            with self.subTest(fine=fine):
                self.assertEqual(scan_source(fine), [])


class HelperAgreementTests(TestCase):
    """The queryset helper and the object helper are two spellings of one
    rule; they must never disagree."""

    def setUp(self):
        self.school = School.objects.create(name="Sweep A")
        self.other_school = School.objects.create(name="Sweep B")

    def _teacher(self, email, school=None):
        return CustomUser.objects.create_user(
            email=email,
            password="Str0ng-sweep-pass!",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            school=school,
            is_active=True,
        )

    def test_the_two_helpers_agree_for_every_ownership_and_school_shape(self):
        me = self._teacher("me@sweep.test", self.school)
        stranger = self._teacher("stranger@sweep.test", self.school)
        own_session = Session.objects.create(name="mine", teacher=me)
        school_a = Session.objects.create(
            name="a", owner_type=SessionOwnerType.SCHOOL, school=self.school
        )
        school_b = Session.objects.create(
            name="b", owner_type=SessionOwnerType.SCHOOL, school=self.other_school
        )
        cases = {
            "own individual": Course.objects.create(
                name="c1", teacher=me, session=own_session
            ),
            "own in my school": Course.objects.create(
                name="c2", teacher=me, session=school_a
            ),
            "own in another school": Course.objects.create(
                name="c3", teacher=me, session=school_b
            ),
            "own without a session": Course.objects.create(name="c4", teacher=me),
            "someone else's": Course.objects.create(
                name="c5", teacher=stranger, session=school_a
            ),
        }
        expected = {
            "own individual": True,
            "own in my school": True,
            "own in another school": False,
            "own without a session": True,
            "someone else's": False,
        }
        # Once with a school, once after removal has cleared it.
        for label, school in (("member", self.school), ("removed", None)):
            CustomUser.objects.filter(pk=me.pk).update(school=school)
            me.refresh_from_db()
            in_q = set(
                Course.objects.filter(teacher_course_access_q(me)).values_list(
                    "name", flat=True
                )
            )
            in_helper = set(reachable_courses(me).values_list("name", flat=True))
            for name, course in cases.items():
                course.refresh_from_db()
                by_object = teacher_can_reach_course(me, course)
                with self.subTest(state=label, case=name):
                    self.assertEqual(by_object, course.name in in_q)
                    self.assertEqual(by_object, course.name in in_helper)
                    if label == "member":
                        self.assertEqual(by_object, expected[name])
                    else:
                        self.assertEqual(
                            by_object,
                            expected[name] and name != "own in my school",
                        )
