"""The assignment page tells the teacher which papers hold a new AI grade
(owner-approved bug fix, 2026-11-12).

Each student row of GET /api/v1/assignments/{id} carries
`has_pending_regrade`: true while an AI re-grade of an already-published
grade is held for the teacher (H-279), false once the teacher publishes it or
edits the grade by hand, and false for a paper that was never re-graded or
never handed in. Teachers and staff only: a student never reads it.

    python manage.py test students.tests_pending_regrade_flag
"""

from django.core.cache import cache
from django.urls import reverse
from rest_framework import status

from students.models import StudentSubmission
from students.tests_h279_regrade_pending_approval import HeldBase

FLAG = "has_pending_regrade"


class PendingRegradeFlagTests(HeldBase):
    def rows(self, user):
        cache.clear()
        self.client.force_authenticate(user)
        response = self.client.get(
            reverse("assignment-detail", kwargs={"pk": self.assignment.pk})
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return response

    def flag(self):
        rows = self.rows(self.teacher).data["student_submissions"]
        mine = [r for r in rows if r["submission_id"] == self.submission.pk]
        self.assertEqual(len(mine), 1, rows)
        return mine[0][FLAG]

    def test_a_held_regrade_reads_true(self):
        self.regrade(3)
        self.assertIs(self.flag(), True)

    def test_a_published_grade_with_nothing_held_reads_false(self):
        self.assertIs(self.flag(), False)

    def test_publishing_the_held_result_turns_it_false(self):
        self.regrade(3)
        self.publish()
        self.assertIs(self.flag(), False)

    def test_publish_all_turns_it_false(self):
        self.regrade(3)
        self.publish_all()
        self.assertIs(self.flag(), False)

    def test_a_hand_edit_turns_it_false(self):
        self.regrade(3)
        self.hand_edit(4)
        self.assertIs(self.flag(), False)

    def test_a_never_published_grade_reads_false(self):
        StudentSubmission.objects.filter(pk=self.submission.pk).update(
            is_published=False
        )
        self.assertIs(self.flag(), False)

    def test_a_student_with_no_submission_reads_false(self):
        StudentSubmission.objects.filter(pk=self.submission.pk).delete()
        rows = self.rows(self.teacher).data["student_submissions"]
        self.assertTrue(rows)
        self.assertTrue(all(r[FLAG] is False for r in rows), rows)

    def test_a_student_never_sees_the_flag(self):
        self.regrade(3)
        response = self.rows(self.student)
        self.assertNotIn(FLAG, response.content.decode())
        listed = self.client.get(reverse("student-submission-list"))
        self.assertNotIn(FLAG, listed.content.decode())
