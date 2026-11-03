"""H-279: approvals that race, in real threads with real row locks.

N3: concurrent single publishes of one held row tell the student once.
N6: publish-all racing a single publish on the same held row tells the
student once.
An approval racing a held save: each held result is released at most once,
and every release has exactly one email and one GRADE_CHANGE.
"""

import threading
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.test import TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from assignments.models import Assignment, AssignmentStatus
from audit.models import AuditEvent
from AutoGrader.testing.concurrency import run_concurrently
from classrooms.models import Course, Session, StudentCourse
from classrooms.tests_final_grade_zero_score import LOCMEM, grading_result, make_user
from students import services
from students.models import StudentSubmission
from users.models import Settings, UserTypes

HELD_MARK = "h279-held-result"


def held_result(score, mark=HELD_MARK):
    result = grading_result(score, 10)
    result["h279_mark"] = mark
    return result


@override_settings(CACHES=LOCMEM)
class RacingApprovals(TransactionTestCase):
    def setUp(self):
        cache.clear()
        self.teacher = make_user("h279-race-t@x.test", UserTypes.TEACHER)
        self.student = make_user("h279-race-s@x.test", UserTypes.STUDENT)
        Settings.objects.update_or_create(
            user=self.student, defaults={"notify_grading_complete": True}
        )
        self.course = Course.objects.create(
            name="Race 7",
            teacher=self.teacher,
            session=Session.objects.create(name="Term", teacher=self.teacher),
        )
        StudentCourse.objects.create(
            student=self.student, course=self.course, enrollment_status="ENROLLED"
        )
        self.assignment = Assignment.objects.create(
            title="Race Quiz",
            course=self.course,
            teacher=self.teacher,
            status=AssignmentStatus.PUBLISHED,
            total_points=10,
            due_date=timezone.now() + timedelta(days=7),
        )
        self.submission = StudentSubmission.objects.create(
            student=self.student, assignment=self.assignment, answers={}
        )
        services._populate_and_save_grade(self.submission, grading_result(7, 10), None)
        StudentSubmission.objects.filter(pk=self.submission.pk).update(
            is_published=True
        )
        self.regrade(3)

        self.lock = threading.Lock()
        self.emails = []

        def sent(**kwargs):
            with self.lock:
                self.emails.append(kwargs)

        mail = patch("students.services.send_email_task.delay", side_effect=sent)
        mail.start()
        self.addCleanup(mail.stop)
        summary = patch("students.services.student_summary_async")
        self.summary = summary.start()
        self.addCleanup(summary.stop)
        self.changes_before = self.grade_changes()

    def regrade(self, score, mark=HELD_MARK):
        row = StudentSubmission.objects.get(pk=self.submission.pk)
        services._populate_and_save_grade(row, held_result(score, mark), None)

    def row(self):
        return StudentSubmission.objects.get(pk=self.submission.pk)

    def grade_changes(self):
        return AuditEvent.objects.filter(
            action="GRADE_CHANGE", target_id=self.submission.pk
        ).count()

    def post(self, name, pk):
        client = APIClient()
        client.force_authenticate(self.teacher)
        response = client.post(reverse(name, kwargs={"pk": pk}))
        return response.status_code

    def publish(self, _=None):
        return self.post("student-submission-publish-grade", self.submission.pk)

    def publish_all(self, _=None):
        return self.post("assignment-publish-all-grades", self.assignment.pk)

    def test_the_fixture_holds_a_result(self):
        row = self.row()
        self.assertEqual(row.score, Decimal("7"))
        self.assertEqual(Decimal(str(row.pending_result["score"])), Decimal("3"))

    def test_n3_concurrent_single_publishes_tell_the_student_once(self):
        results, errors = run_concurrently(self.publish, 8, test=self)

        self.assertEqual(errors, [])
        self.assertEqual(results, [200] * 8)
        self.assertEqual(len(self.emails), 1)
        self.assertEqual(self.grade_changes(), self.changes_before + 1)
        self.assertEqual(self.summary.delay.call_count, 1)
        row = self.row()
        self.assertEqual(row.score, Decimal("3"))
        self.assertIsNone(row.pending_result)

    def test_n6_publish_all_racing_single_publish_tells_the_student_once(self):
        def either(index):
            return self.publish_all() if index % 2 else self.publish()

        results, errors = run_concurrently(either, 8, test=self)

        self.assertEqual(errors, [])
        self.assertEqual(results, [200] * 8)
        self.assertEqual(len(self.emails), 1)
        self.assertEqual(self.grade_changes(), self.changes_before + 1)
        self.assertEqual(self.row().score, Decimal("3"))

    def test_an_approval_racing_a_held_save_releases_each_result_at_most_once(self):
        """Thread 0 saves a newer held result (R2, 5); the others publish.
        Whatever the order, the released grade is R1 or R2, a result is
        never released twice, and each release told the student once."""
        for _ in range(5):
            self.regrade(3, mark="R1")
            self.emails.clear()
            before = self.grade_changes()

            def act(index):
                if index == 0:
                    self.regrade(5, mark="R2")
                    return "saved"
                return self.publish()

            results, errors = run_concurrently(act, 6, test=self)

            self.assertEqual(errors, [])
            row = self.row()
            releases = self.grade_changes() - before
            self.assertEqual(len(self.emails), releases)
            if row.pending_result is None:
                # R2 was saved first (or R1 released, then R2 released)
                self.assertEqual(row.feedback["h279_mark"], "R2")
                self.assertIn(releases, (1, 2))
            else:
                # R1 released; R2 held after every publish had run
                self.assertEqual(row.feedback["h279_mark"], "R1")
                self.assertEqual(row.pending_result["feedback"]["h279_mark"], "R2")
                self.assertEqual(releases, 1)
            # put the row back to "released 7, nothing held" for the next round
            StudentSubmission.objects.filter(pk=self.submission.pk).update(
                pending_result=None, pending_graded_at=None
            )
