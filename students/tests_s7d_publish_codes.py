"""
Epic A S7d, QA catalogue section F: publishing grades.

A submission is publishable only when grading finished (graded_at AND a
score). The single publish refuses any other with a coded 400
SUBMISSION_NOT_GRADED; publish-all lists every submission it can't publish
as a skipped item with the same code (it used to only count them).
"""

from unittest.mock import patch

from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from students.models import StudentSubmission
from students.tests_grading_review_fixes import GradingFixtureMixin

MESSAGE = "This submission hasn't been graded yet, so it can't be published."
REMEDIATION = "Grade it first, then publish."


def notify_nobody(submission):
    """Stands in for the student notification (not a MagicMock)."""


class SinglePublishTests(GradingFixtureMixin, APITestCase):
    def setUp(self):
        self.teacher = self._make_teacher("s7d-publish")
        self.assignment = self._make_assignment(self.teacher)
        self.submission = self._make_submission(
            self.assignment, self._make_student("s7d-publish")
        )
        self.url = reverse(
            "student-submission-publish-grade", kwargs={"pk": self.submission.pk}
        )
        self.client.force_authenticate(user=self.teacher)

    def test_every_not_fully_graded_state_is_refused_with_the_code(self):
        now = timezone.now()
        for label, fields in (
            ("never graded", {"graded_at": None, "score": None}),
            ("graded_at only", {"graded_at": now, "score": None}),
            ("score only", {"graded_at": None, "score": 8}),
        ):
            with self.subTest(label):
                StudentSubmission.objects.filter(pk=self.submission.pk).update(**fields)

                response = self.client.post(self.url)

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                body = response.json()
                envelope = body["error"]["field_errors"]
                self.assertEqual(envelope["reason_code"], "SUBMISSION_NOT_GRADED")
                self.assertEqual(envelope["error_class"], "USER")
                self.assertEqual(envelope["remediation"], REMEDIATION)
                self.assertIs(envelope["retryable"], False)
                self.assertEqual(envelope["params"], {})
                self.assertEqual(envelope["reference"], response["X-Request-ID"])
                self.assertEqual(body["message"], MESSAGE)
                self.submission.refresh_from_db()
                self.assertFalse(self.submission.is_published)

    @patch("students.views.notify_student_of_graded_submission", new=notify_nobody)
    def test_a_graded_submission_still_publishes(self):
        StudentSubmission.objects.filter(pk=self.submission.pk).update(
            graded_at=timezone.now(), score=8
        )

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.submission.refresh_from_db()
        self.assertTrue(self.submission.is_published)


@patch("students.services.notify_student_of_graded_submission", new=notify_nobody)
class PublishAllTests(GradingFixtureMixin, APITestCase):
    def setUp(self):
        self.teacher = self._make_teacher("s7d-bulk")
        self.assignment = self._make_assignment(self.teacher)
        self.url = reverse(
            "assignment-publish-all-grades", kwargs={"pk": self.assignment.pk}
        )
        self.client.force_authenticate(user=self.teacher)

    def submission(self, tag, **fields):
        return self._make_submission(
            self.assignment, self._make_student(f"s7d-{tag}"), **fields
        )

    def assert_skipped(self, entry, submission):
        self.assertEqual(
            entry,
            {
                "submission_id": str(submission.pk),
                "student_id": str(submission.student_id),
                "status": "skipped",
                "error": MESSAGE,
                "reason_code": "SUBMISSION_NOT_GRADED",
                "error_class": "USER",
                "message": MESSAGE,
                "remediation": REMEDIATION,
                "retryable": False,
                "params": {},
                "reference": entry["reference"],
            },
        )

    def test_every_submission_not_published_is_listed_with_its_code(self):
        now = timezone.now()
        graded = self.submission("graded", graded_at=now, score=8)
        half = self.submission("half", graded_at=now, score=None)
        never = self.submission("never")

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data
        self.assertEqual((data["total_graded"], data["ungraded_count"]), (1, 2))
        skipped = {entry["submission_id"]: entry for entry in data["skipped"]}
        self.assertEqual(set(skipped), {str(half.pk), str(never.pk)})
        self.assert_skipped(skipped[str(half.pk)], half)
        self.assert_skipped(skipped[str(never.pk)], never)
        self.assertEqual(skipped[str(half.pk)]["reference"], response["X-Request-ID"])
        graded.refresh_from_db()
        half.refresh_from_db()
        self.assertTrue(graded.is_published)
        self.assertFalse(half.is_published)

    def test_nothing_graded_is_still_a_normal_answer_listing_the_skipped(self):
        never = self.submission("never")

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data["message"], "No graded submissions found to publish."
        )
        [entry] = response.data["skipped"]
        self.assert_skipped(entry, never)

    def test_everything_graded_skips_nothing(self):
        self.submission("graded", graded_at=timezone.now(), score=8)

        response = self.client.post(self.url)

        self.assertEqual(response.data["skipped"], [])
        self.assertEqual(response.data["ungraded_count"], 0)
