"""
Cache freshness for the submission paths that write with QuerySet.update().

`post_save` never fires for a queryset update, so
students.signals.clear_student_submission_cache never runs for these
writes; each call site must invoke `invalidate_submission_caches` itself.
While the legacy wildcard sweeps still run they would mask a missing call
(a `studentsubmissions:*` delete rescues the stale entry), so - exactly as
AutoGrader/tests_cache_dashboard_freshness.py::LegacyDisabledFreshnessTests
does - these tests neutralise `delete_cache_patterns` in every signal module
and require the generation counters alone to carry the freshness. Real
Redis, real endpoints. Removing any of the three `invalidate_submission_caches`
calls turns its test red.

Run with:
    python manage.py test students.tests_submission_update_freshness
"""

from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from assignments.models import Assignment, AssignmentStatus
from AutoGrader.test_cache import real_redis_caches
from classrooms.models import Course, Session
from students.models import GradingState, StudentSubmission
from students.services import _claim_submission_for_grading, _mark_grading_claim_failed
from users.models import CustomUser, UserTypes

REDIS_CACHE = real_redis_caches("redis://127.0.0.1:6379/7")


@override_settings(CACHES=REDIS_CACHE)
class SubmissionUpdatePathFreshnessTest(TransactionTestCase):
    def setUp(self):
        cache.clear()
        self.teacher = CustomUser.objects.create_user(
            email="fresh-teacher@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        student = CustomUser.objects.create_user(
            email="fresh-student@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
        )
        session = Session.objects.create(name="S", teacher=self.teacher)
        course = Course.objects.create(name="C", teacher=self.teacher, session=session)
        assignment = Assignment.objects.create(
            title="A",
            course=course,
            status=AssignmentStatus.PUBLISHED,
            questions=[{"question_number": 1, "question_text": "Q?", "points": 10}],
        )
        self.submission = StudentSubmission.objects.create(
            assignment=assignment,
            student=student,
            answers=[{"question_number": 1, "answer_html": "x"}],
            score=8,
            graded_at=timezone.now() - timedelta(minutes=1),
            feedback={"grading_summary": {"total_score": 8, "max_total_points": 10}},
            needs_review=True,
            review_reasons=[{"type": "grader_disagreement"}],
            review_severity=0.9,
            review_tier="critical",
        )
        self.detail_url = reverse(
            "student-submission-detail", kwargs={"pk": self.submission.pk}
        )
        self.client = APIClient()
        self.client.force_authenticate(self.teacher)

        # Legacy sweeps OFF in every module that holds a reference - the
        # generation counters must do the work alone.
        self._patches = [
            patch(f"{module}.delete_cache_patterns", lambda *a, **k: None)
            for module in (
                "classrooms.signals",
                "users.signals",
                "students.signals",
                "assignments.signals",
            )
        ]
        for p in self._patches:
            p.start()
        self.addCleanup(self._stop_patches)

    def _stop_patches(self):
        for p in self._patches:
            p.stop()

    def tearDown(self):
        cache.clear()

    def _detail(self):
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, 200)
        return response.data

    def test_the_legacy_mechanism_really_is_disabled(self):
        cache.set("studentsubmissions:user_id__sentinel:instance_id__x", "cached", 300)
        self.submission.save(update_fields=["score"])
        self.assertEqual(
            cache.get("studentsubmissions:user_id__sentinel:instance_id__x"),
            "cached",
            "a legacy wildcard sweep still ran - the patch did not take",
        )

    def test_publish_refreshes_the_cached_detail(self):
        self.assertFalse(self._detail()["is_published"])  # now cached

        response = self.client.post(
            reverse(
                "student-submission-publish-grade", kwargs={"pk": self.submission.pk}
            )
        )
        self.assertEqual(response.status_code, 200)

        self.assertTrue(self._detail()["is_published"])

    def test_mark_reviewed_refreshes_the_cached_detail(self):
        self.assertTrue(self._detail()["needs_review"])  # now cached

        response = self.client.post(
            reverse(
                "student-submission-mark-reviewed", kwargs={"pk": self.submission.pk}
            )
        )
        self.assertEqual(response.status_code, 200)

        self.assertFalse(self._detail()["needs_review"])

    def test_failed_grading_claim_refreshes_the_cached_detail(self):
        _claim_submission_for_grading(self.submission.pk)
        self.assertEqual(self._detail()["grading_state"], GradingState.RUNNING)

        _mark_grading_claim_failed(self.submission.pk)

        self.assertEqual(self._detail()["grading_state"], GradingState.FAILED)
