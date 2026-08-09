"""
Regression coverage for the review-fix batch that followed the C2/C3/H1-H4
hardening work:

- publish endpoints: a submission is publishable only when grading actually
  finished (graded_at AND score — the old guard's OR let half-graded rows
  through), a double-publish notifies the student exactly once (conditional
  UPDATE claim instead of read-then-write), and bulk publish
  (assignments/views.py::publish_all_grades) now notifies newly published
  students at all (its .update() bypasses post_save, which previously meant
  no notifications and no cache invalidation, unlike single publish).

- update-grade: a PATCH without "score" is a clean 400, not a KeyError 500
  (partial=True makes every serializer field optional), and the regrade's
  formatted-grade regeneration is a tracked BackgroundProcessingTask like
  every other call site instead of an untrackable bare .delay().

- retrieve: the lazy raw_input backfill on GET persists via queryset
  .update(), so a read no longer fires post_save (which recalculated the
  course final grade and pattern-deleted every dashboard cache on every
  uncached detail view).

- school-admin teacher_detail: a teacher with no CreditWallet row (the
  reverse OneToOne raises DoesNotExist, not AttributeError) renders with
  zero usage instead of a 500.

Run with:
    python manage.py test students.tests_grading_review_fixes
"""

from unittest.mock import patch

from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from assignments.models import Assignment
from classrooms.models import Course, School, Session
from students.models import (
    BackgroundProcessingTask,
    BackgroundTaskType,
    StudentSubmission,
)
from users.models import CustomUser, UserTypes


class GradingFixtureMixin:
    """Fixture shape mirrors students.tests_grading_idempotency."""

    def _make_teacher(self, tag):
        return CustomUser.objects.create_user(
            email=f"{tag}-teacher-{timezone.now().timestamp()}@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )

    def _make_student(self, tag):
        return CustomUser.objects.create_user(
            email=f"{tag}-student-{timezone.now().timestamp()}@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
        )

    def _make_assignment(self, teacher):
        session = Session.objects.create(name="S", teacher=teacher)
        course = Course.objects.create(name="C", teacher=teacher, session=session)
        return Assignment.objects.create(
            title="A",
            course=course,
            questions=[{"question_number": 1, "question_text": "Q1?", "points": 10}],
        )

    def _make_submission(self, assignment, student, **overrides):
        submission = StudentSubmission.objects.create(
            assignment=assignment,
            student=student,
            answers=[{"question_number": 1, "answer_html": "An answer."}],
        )
        if overrides:
            StudentSubmission.objects.filter(pk=submission.pk).update(**overrides)
            submission.refresh_from_db()
        return submission


class PublishGradeGuardTest(GradingFixtureMixin, APITestCase):
    def setUp(self):
        self.teacher = self._make_teacher("publish")
        self.assignment = self._make_assignment(self.teacher)
        self.submission = self._make_submission(
            self.assignment, self._make_student("publish")
        )
        self.url = reverse(
            "student-submission-publish-grade", kwargs={"pk": self.submission.pk}
        )
        self.client.force_authenticate(user=self.teacher)

    def test_half_graded_submission_graded_at_only_is_rejected(self):
        # A run that died between setting graded_at and persisting a score.
        StudentSubmission.objects.filter(pk=self.submission.pk).update(
            graded_at=timezone.now(), score=None
        )
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.submission.refresh_from_db()
        self.assertFalse(self.submission.is_published)

    def test_half_graded_submission_score_only_is_rejected(self):
        StudentSubmission.objects.filter(pk=self.submission.pk).update(
            graded_at=None, score=8
        )
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch("students.views.notify_student_of_graded_submission")
    def test_fully_graded_submission_publishes_and_notifies_once(self, mock_notify):
        StudentSubmission.objects.filter(pk=self.submission.pk).update(
            graded_at=timezone.now(), score=8
        )
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.submission.refresh_from_db()
        self.assertTrue(self.submission.is_published)
        self.assertEqual(mock_notify.call_count, 1)

    @patch("students.views.notify_student_of_graded_submission")
    def test_second_publish_does_not_renotify(self, mock_notify):
        # The conditional-UPDATE claim: only the request that actually flips
        # is_published sends the email, so a double click (or two racing
        # requests, which both read is_published=False) can't duplicate it.
        StudentSubmission.objects.filter(pk=self.submission.pk).update(
            graded_at=timezone.now(), score=8
        )
        first = self.client.post(self.url)
        second = self.client.post(self.url)
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(mock_notify.call_count, 1)


class PublishAllGradesTest(GradingFixtureMixin, APITestCase):
    def setUp(self):
        self.teacher = self._make_teacher("bulk")
        self.assignment = self._make_assignment(self.teacher)
        now = timezone.now()
        self.fully_graded = self._make_submission(
            self.assignment, self._make_student("bulk1"), graded_at=now, score=8
        )
        self.half_graded = self._make_submission(
            self.assignment, self._make_student("bulk2"), graded_at=now, score=None
        )
        self.already_published = self._make_submission(
            self.assignment,
            self._make_student("bulk3"),
            graded_at=now,
            score=6,
            is_published=True,
        )
        self.url = reverse(
            "assignment-publish-all-grades", kwargs={"pk": self.assignment.pk}
        )
        self.client.force_authenticate(user=self.teacher)

    @patch("students.services.notify_student_of_graded_submission")
    def test_bulk_publish_skips_half_graded_and_notifies_only_newly_published(
        self, mock_notify
    ):
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.fully_graded.refresh_from_db()
        self.half_graded.refresh_from_db()
        self.assertTrue(self.fully_graded.is_published)
        # graded_at without a score is a failed run, not a publishable grade.
        self.assertFalse(self.half_graded.is_published)

        # Exactly one notification: the newly published submission. The
        # already-published one must not be re-notified, and .update()
        # bypassing post_save must no longer mean zero notifications.
        self.assertEqual(mock_notify.call_count, 1)
        notified = mock_notify.call_args[0][0]
        self.assertEqual(notified.pk, self.fully_graded.pk)


class UpdateGradeValidationTest(GradingFixtureMixin, APITestCase):
    def setUp(self):
        from datetime import timedelta

        from billing.models import CreditBucket, CreditBucketType, CreditWallet

        self.teacher = self._make_teacher("regrade")
        wallet, _ = CreditWallet.objects.get_or_create(user=self.teacher)
        CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=100_000,
            used_credits=0,
            expires_at=timezone.now() + timedelta(days=30),
        )
        self.assignment = self._make_assignment(self.teacher)
        self.submission = self._make_submission(
            self.assignment,
            self._make_student("regrade"),
            graded_at=timezone.now(),
            score=8,
            max_points=10,
            feedback={
                "grading_summary": {
                    "total_score": 8,
                    "max_total_points": 10,
                    "percentage": 80.0,
                }
            },
        )
        self.url = reverse(
            "student-submission-update-grade", kwargs={"pk": self.submission.pk}
        )
        self.client.force_authenticate(user=self.teacher)

    def test_patch_without_score_is_400_not_500(self):
        # partial=True makes "score" optional at the serializer layer, so
        # the view must guard the missing key itself.
        response = self.client.patch(self.url, data={}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("score", str(response.data).lower())

    @patch("students.views.launch_processing_task")
    def test_valid_patch_updates_grade_and_tracks_formatting_task(self, mock_launch):
        response = self.client.patch(self.url, data={"score": 5}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.submission.refresh_from_db()
        self.assertEqual(float(self.submission.score), 5.0)
        self.assertEqual(float(self.submission.score_percentage), 50.0)
        self.assertTrue(self.submission.was_regraded)

        # The formatted-grade regeneration is a tracked processing task,
        # not an untrackable bare .delay().
        self.assertEqual(mock_launch.call_count, 1)
        self.assertTrue(
            BackgroundProcessingTask.objects.filter(
                submission=self.submission,
                task_type=BackgroundTaskType.FORMATTED_GRADE,
            ).exists()
        )


class RetrieveBackfillSignalTest(GradingFixtureMixin, APITestCase):
    def setUp(self):
        self.teacher = self._make_teacher("retrieve")
        self.assignment = self._make_assignment(self.teacher)
        self.submission = self._make_submission(
            self.assignment, self._make_student("retrieve")
        )
        self.url = reverse(
            "student-submission-detail", kwargs={"pk": self.submission.pk}
        )
        self.client.force_authenticate(user=self.teacher)

    @patch("students.views.AssignmentProcessingService.html_to_prosemirror_json")
    @patch("classrooms.signals._recalculate_final_grade")
    def test_get_backfills_raw_input_without_firing_post_save(
        self, mock_recalc, mock_convert
    ):
        mock_convert.return_value = {"type": "doc", "content": []}
        self.assertFalse(self.submission.raw_input)

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Persisted...
        self.submission.refresh_from_db()
        self.assertTrue(self.submission.raw_input)
        # ...without the post_save cascade (final-grade recalc + pattern
        # cache flush) that a read must never trigger.
        mock_recalc.assert_not_called()


class TeacherDetailNoWalletTest(GradingFixtureMixin, APITestCase):
    def setUp(self):
        self.school = School.objects.create(name=f"School-{timezone.now().timestamp()}")
        self.admin = CustomUser.objects.create_user(
            email=f"admin-{timezone.now().timestamp()}@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.SCHOOL_ADMIN,
            school=self.school,
        )
        self.teacher = self._make_teacher("nowallet")
        self.teacher.school = self.school
        self.teacher.save(update_fields=["school"])
        # The user-creation signal auto-provisions a wallet — delete it so
        # this test actually exercises the DoesNotExist path (the real
        # regression case: signal failure or pre-signal legacy data).
        from billing.models import CreditWallet

        CreditWallet.objects.filter(user=self.teacher).delete()
        self.url = reverse(
            "school-admin-teacher-detail", kwargs={"teacher_id": self.teacher.id}
        )
        self.client.force_authenticate(user=self.admin)

    def test_teacher_without_wallet_renders_with_zero_usage(self):
        # The reverse OneToOne raises CreditWallet.DoesNotExist for a
        # teacher who never touched a credit feature — must be zeros, not
        # a 500.
        self.assertFalse(hasattr(self.teacher, "_credit_wallet_cache"))
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["credits_used"], 0)
        self.assertEqual(float(response.data["credits_used_percentage"]), 0.0)
