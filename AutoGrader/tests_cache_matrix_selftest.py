"""H-1 Stage 3: prove the freshness matrix can tell stale from fresh.

A matrix that reports "no stale data" is only evidence if the same machinery
reports STALE when data IS stale. These tests feed it writes whose outcome is
known in advance:

* a `QuerySet.update()` with no bump: the database moves, nothing
  invalidates, so it MUST be reported STALE;
* the same update followed by an explicit generation bump of exactly the
  affected viewers: FRESH for them, UNAFFECTED for an unrelated teacher;
* a bump with no data change: UNAFFECTED, not SPURIOUS;
* a read whose payload changes between two reads of unchanged data: the
  harness refuses to classify it;
* the no-wildcard guard `run_matrix` applies to every mutation (H-1 step 4)
  fails on a wildcard SCAN, lets the documented PDF exact-prefix clear
  through, and a routed write now issues no SCAN at all.

Rows are created through the real endpoints (session, course, enrolment by
email), per the Stage 3 fixture rule.

Real Redis + real Postgres.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TransactionTestCase
from django.urls import reverse
from rest_framework.test import APIClient

from AutoGrader.cache_generation import SCOPE_USER, bump_many
from AutoGrader.tests_cache_matrix_support import (
    FRESH,
    STALE,
    UNAFFECTED,
    FreshnessMatrixMixin,
    Read,
)
from classrooms.models import Course, EnrollmentStatusType, StudentCourse
from users.models import UserTypes

User = get_user_model()


def make_active_user(email, user_type):
    return User.objects.create_user(
        email=email,
        password="password123",  # nosec  # pragma: allowlist secret
        user_type=user_type,
        is_active=True,
        first_name="Matrix",
        last_name=user_type.title(),
    )


class MatrixFixtureBase(FreshnessMatrixMixin, TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        cache.clear()
        # Enrolment notifications would enqueue onto the (prefixed) test
        # broker; the matrix is about cache state, not mail.
        self.enterContext(
            patch("classrooms.services.notifications.safe_delay", lambda *a, **k: None)
        )

        self.teacher = make_active_user("mx-t@x.test", UserTypes.TEACHER)
        self.other_teacher = make_active_user("mx-t2@x.test", UserTypes.TEACHER)
        self.student = make_active_user("mx-s@x.test", UserTypes.STUDENT)

        self.course = self.create_course_as(self.teacher, "MX 101")
        self.create_course_as(self.other_teacher, "MX 201")
        self.enrol_as(self.teacher, self.course, self.student.email)

        self.course_list = reverse("course-list")

    def create_course_as(self, teacher, name):
        client = APIClient()
        client.force_authenticate(teacher)
        session = client.post(reverse("session-list"), {"name": f"{name} term"})
        self.assertEqual(session.status_code, 201, session.content)
        course = client.post(
            (
                self.course_list
                if hasattr(self, "course_list")
                else reverse("course-list")
            ),
            {"name": name, "session": session.data["id"], "description": "d"},
        )
        self.assertEqual(course.status_code, 201, course.content)
        return Course.objects.get(pk=course.data["id"])

    def enrol_as(self, teacher, course, email):
        client = APIClient()
        client.force_authenticate(teacher)
        response = client.post(
            reverse("course-students", args=[course.pk]), {"email": email}
        )
        self.assertIn(response.status_code, (200, 201), response.content)
        self.assertEqual(
            StudentCourse.objects.get(
                student__email=email, course=course
            ).enrollment_status,
            EnrollmentStatusType.ENROLLED,
        )

    def reads(self):
        return [
            Read("student course list", self.student, self.course_list),
            Read("teacher course list", self.teacher, self.course_list),
            Read("other teacher course list", self.other_teacher, self.course_list),
        ]

    def withdraw_without_invalidation(self):
        updated = StudentCourse.objects.filter(
            student=self.student, course=self.course
        ).update(enrollment_status=EnrollmentStatusType.WITHDRAWN)
        self.assertEqual(updated, 1)


class MatrixDetectsStalenessTests(MatrixFixtureBase):
    def test_an_uninvalidated_write_is_reported_stale(self):
        result = self.run_matrix(
            "withdraw via .update(), no bump",
            self.reads(),
            self.withdraw_without_invalidation,
        )
        verdicts = {o.label: o.verdict for o in result.outcomes}
        self.assertEqual(
            verdicts,
            {
                "student course list": STALE,
                "teacher course list": STALE,
                "other teacher course list": UNAFFECTED,
            },
            result.table(),
        )
        with self.assertRaises(AssertionError) as caught:
            self.assert_no_stale(result)
        self.assertIn("student course list", str(caught.exception))
        self.assertIn("teacher course list", str(caught.exception))

    def test_invalidating_exactly_the_affected_viewers_is_reported_fresh(self):
        def withdraw_and_bump():
            self.withdraw_without_invalidation()
            bump_many([(SCOPE_USER, self.student.pk), (SCOPE_USER, self.teacher.pk)])

        result = self.run_matrix(
            "withdraw + precise bump", self.reads(), withdraw_and_bump
        )
        verdicts = {o.label: o.verdict for o in result.outcomes}
        self.assertEqual(
            verdicts,
            {
                "student course list": FRESH,
                "teacher course list": FRESH,
                "other teacher course list": UNAFFECTED,
            },
            result.table(),
        )
        self.assert_no_stale(
            result, expect_changed=["student course list", "teacher course list"]
        )
        self.assert_no_scan(result)

    def test_a_bump_without_a_data_change_is_unaffected_not_spurious(self):
        result = self.run_matrix(
            "bump only",
            self.reads(),
            lambda: bump_many([(SCOPE_USER, self.student.pk)]),
        )
        self.assertEqual(
            {o.verdict for o in result.outcomes}, {UNAFFECTED}, result.table()
        )

    def test_expect_changed_fails_when_the_mutation_moves_nothing(self):
        result = self.run_matrix("no-op", self.reads(), lambda: None)
        with self.assertRaises(AssertionError) as caught:
            self.assert_no_stale(result, expect_changed=["student course list"])
        self.assertIn("did not move", str(caught.exception))

    def test_a_payload_that_changes_on_every_read_is_refused(self):
        calls = {"n": 0}
        original = self.get_as

        def flaky_get_as(user, url):
            response = original(user, url)
            calls["n"] += 1
            response.status_code = 200 + calls["n"] % 2
            return response

        self.get_as = flaky_get_as  # type: ignore[method-assign]
        with self.assertRaises(AssertionError) as caught:
            self.run_matrix("flaky", self.reads()[:1], lambda: None)
        self.assertIn("cannot be classified", str(caught.exception))


class NoWildcardGuardTests(MatrixFixtureBase):
    """The SCAN guard that replaced `legacy_wildcards_disabled()`.

    Until H-1 step 4 this class proved the spy counted the legacy receivers'
    SCANs (a routed course rename swept the keyspace). The wildcards are
    gone, so it now proves the opposite on the same write, and that the
    guard `run_matrix` applies to every mutation is not vacuous: it fails on
    a wildcard and lets only the documented PDF exact-prefix clear through.
    """

    def test_routed_course_rename_issues_no_scan_and_is_fresh(self):
        client = APIClient()
        client.force_authenticate(self.teacher)

        def rename():
            response = client.patch(
                reverse("course-detail", args=[self.course.pk]),
                {"name": "MX 101 renamed"},
            )
            self.assertEqual(response.status_code, 200, response.content)

        result = self.run_matrix("course rename", self.reads(), rename)
        self.assert_no_scan(result)
        self.assertEqual(result.scan_patterns_during_mutation, [])
        self.assert_no_stale(
            result, expect_changed=["student course list", "teacher course list"]
        )

    def test_a_wildcard_delete_during_a_mutation_fails_the_matrix(self):
        def rename_with_a_wildcard():
            Course.objects.filter(pk=self.course.pk).update(name="MX 101 swept")
            cache.delete_pattern("*user*")

        with self.assertRaises(AssertionError) as caught:
            self.run_matrix("wildcard", self.reads(), rename_with_a_wildcard)
        self.assertIn("*user*", str(caught.exception))
        self.assertIn("other than the PDF exact-prefix clear", str(caught.exception))

    def test_the_pdf_exact_prefix_clear_is_the_one_allowed_scan(self):
        from assignments.models import Assignment
        from assignments.pdf_cache import invalidate_assignment_pdfs

        assignment = Assignment.objects.create(title="PDF", course=self.course)

        # Only the PDF clear itself runs inside the matrix mutation.
        with patch("assignments.pdf_cache._enabled", return_value=True):
            result = self.run_matrix(
                "pdf clear",
                self.reads(),
                lambda: invalidate_assignment_pdfs(assignment.id),
            )
        self.assertEqual(len(set(result.scan_patterns_during_mutation)), 1)
        self.assertIn(
            f":assignmentpdf:v1:{assignment.id}:*",
            result.scan_patterns_during_mutation[0],
        )
        # The strict variant still refuses it, for mutations that save no
        # Assignment and so have no reason to scan at all.
        with self.assertRaises(AssertionError):
            self.assert_no_scan(result)
