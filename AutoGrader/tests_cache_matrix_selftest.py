"""H-1 Stage 3: prove the freshness matrix can tell stale from fresh.

A matrix that reports "no stale data" is only evidence if the same machinery
reports STALE when data IS stale. These tests feed it writes whose outcome is
known in advance:

* a `QuerySet.update()` with the legacy wildcards disabled and no bump: the
  database moves, nothing invalidates, so it MUST be reported STALE;
* the same update followed by an explicit generation bump of exactly the
  affected viewers: FRESH for them, UNAFFECTED for an unrelated teacher;
* a bump with no data change: UNAFFECTED, not SPURIOUS;
* a read whose payload changes between two reads of unchanged data: the
  harness refuses to classify it;
* with the legacy mechanism still live, a routed write issues a keyspace
  SCAN and the spy counts it, so the no-SCAN assertion can fail.

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
    legacy_wildcards_disabled,
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
    disable_legacy = True

    def setUp(self):
        cache.clear()
        if self.disable_legacy:
            patched = self.enterContext(legacy_wildcards_disabled())
            self.assertTrue(patched, "no legacy module was patched")
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


class ScanSpyCountsLegacyWildcardsTests(MatrixFixtureBase):
    """With the legacy receivers LIVE, a routed write sweeps the keyspace.

    This is the "before" side of the Stage 3 measurement and proves the
    zero-SCAN assertion is not vacuous. Once Stage 3 removes the wildcards
    this class is expected to change: it becomes the "after" proof.
    """

    disable_legacy = False

    def test_routed_course_rename_is_counted_by_the_scan_spy(self):
        client = APIClient()
        client.force_authenticate(self.teacher)

        def rename():
            response = client.patch(
                reverse("course-detail", args=[self.course.pk]),
                {"name": "MX 101 renamed"},
            )
            self.assertEqual(response.status_code, 200, response.content)

        result = self.run_matrix("course rename (legacy live)", self.reads(), rename)
        self.assertGreater(
            result.scans_during_mutation, 0, result.commands_during_mutation
        )
        with self.assertRaises(AssertionError):
            self.assert_no_scan(result)
