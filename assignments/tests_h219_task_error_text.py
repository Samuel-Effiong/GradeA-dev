"""H-219: the last places in assignments/tasks.py that kept or logged an
error's own text, or its traceback.

Found by Verifier 2 reading H-209 (docs/evidence/h209-task-results-no-text/):
`upload_assignment_async` stored `str(e)` in the batch session's results,
`grade_batch_async` logged "Failed to clear scheduling info or create session:
{e}", and four `logger.exception` sites (the two due-reminder sends, the
new-assignment notice, the PDF pre-render) logged the text and the traceback.
An error's text is whatever the code that raised it wrote: a broker error's
can carry the broker's address (H-208's reason).

The rule now (the Senior Manager's ruling, 2026-10-25): the stored copy is the
tracked row's own sentence (`describe_background_task_error`), and each log
line names the error by class and code location only (`describe_error_for_log`,
H-208/H-209's form), still at ERROR. Return values, statuses and the tracked
rows do not change.
"""

import base64
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from assignments import pdf_cache
from assignments.models import Assignment, AssignmentStatus
from assignments.tasks import (
    grade_batch_async,
    prerender_assignment_pdfs,
    send_assignment_due_reminder,
    send_new_assignment_posted_notification,
    upload_assignment_async,
)
from assignments.tests_download_pdf import objective_question
from assignments.tests_rigor import RigorFixtureMixin
from assignments.tests_upload_batch_billing import funded_teacher_with_course
from classrooms.models import Course, EnrollmentStatusType, Session, StudentCourse
from students.models import (
    BackgroundProcessingTask,
    BackgroundTaskType,
    BatchUploadSession,
    BatchUploadType,
)
from users.models import CustomUser, UserTypes

MARKER = "h219-made-up-error-text"


def failing_with_the_marker():
    return RuntimeError(MARKER)


class UploadFailureStoresNoTextTest(TestCase):
    def test_a_failed_upload_stores_the_tracked_rows_sentence_not_the_text(self):
        """S1 (assignments/tasks.py upload_assignment_async)."""
        teacher, course = funded_teacher_with_course("h219")
        session = BatchUploadSession.objects.create(
            teacher=teacher,
            course=course,
            task_type=BatchUploadType.ASSIGNMENT,
            total_files=1,
        )
        tracked = BackgroundProcessingTask.objects.create(
            requested_by=teacher,
            task_type=BackgroundTaskType.BATCH_ASSIGNMENT_UPLOAD,
            batch_session=session,
        )

        with patch(
            "assignments.tasks.upload_assignment_file",
            side_effect=failing_with_the_marker(),
        ):
            outcome = upload_assignment_async.apply(
                kwargs={
                    "user_id": str(teacher.id),
                    "course_id": str(course.id),
                    "session_id": str(session.id),
                    "file_payload": {
                        "name": "a.png",
                        "content_type": "image/png",
                        "content_b64": base64.b64encode(b"h219").decode("ascii"),
                    },
                    "prompt_text": "prompt",
                    "file_name": "a.png",
                    "processing_task_id": str(tracked.id),
                }
            )

        session.refresh_from_db()
        tracked.refresh_from_db()
        [entry] = session.results
        self.assertNotIn(MARKER, entry["error"])
        self.assertEqual(entry["error"], tracked.error)
        self.assertEqual(entry["status"], "FAILED")
        self.assertTrue(outcome.failed())


class ClassOnlyLogAssertions(TestCase):
    def assert_class_only(self, logs, marker):
        """The one ERROR line holding `marker`: no traceback, no text, the
        error's class named."""
        [record] = [r for r in logs.records if marker in r.getMessage()]
        self.assertIsNone(record.exc_info)
        self.assertNotIn(MARKER, "\n".join(logs.output))
        self.assertEqual(record.levelname, "ERROR")
        self.assertIn("RuntimeError", record.getMessage())
        return record


class BatchLogLineCarriesNoTextTest(TestCase):
    def test_a_failed_batch_session_is_logged_by_class_and_id(self):
        """S2 (grade_batch_async): the assignment read fails."""
        assignment_id = str(uuid.uuid4())

        with patch.object(
            Assignment.objects, "get", side_effect=failing_with_the_marker()
        ), self.assertLogs("assignments.tasks", "ERROR") as logs:
            grade_batch_async.apply(args=(str(uuid.uuid4()), assignment_id))

        [line] = [text for text in logs.output if "Failed to clear" in text]
        self.assertNotIn(MARKER, line)
        self.assertIn("RuntimeError", line)
        self.assertIn(assignment_id, line)


class NotificationFixture(TestCase):
    def setUp(self):
        self.teacher = CustomUser.objects.create_user(
            email="teacher-h219@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            first_name="Notice",
            last_name="Teacher",
        )
        self.student = CustomUser.objects.create_user(
            email="student-h219@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
            first_name="Notice",
            last_name="Student",
        )
        session = Session.objects.create(name="H-219 Session", teacher=self.teacher)
        course = Course.objects.create(
            name="H-219 Course", teacher=self.teacher, session=session
        )
        StudentCourse.objects.create(
            student=self.student,
            course=course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )
        self.assignment = Assignment.objects.create(
            title="H-219 Assignment",
            course=course,
            status=AssignmentStatus.PUBLISHED,
            due_date=timezone.now() + timedelta(days=2),
        )

    def notify(self, user, **flags):
        for name, value in flags.items():
            setattr(user.settings, name, value)
        user.settings.save(update_fields=list(flags))


class NotificationLogLinesCarryNoTextTest(ClassOnlyLogAssertions, NotificationFixture):

    def test_a_failed_teacher_reminder_is_logged_by_class_only(self):
        """S3 (send_assignment_due_reminder, the teacher's send)."""
        self.notify(self.teacher, notify_assignment_due_reminder=True)
        self.notify(self.student, notify_assignment_due_reminder=False)

        with patch(
            "assignments.tasks.send_email_task.delay",
            side_effect=failing_with_the_marker(),
        ), self.assertLogs("assignments.tasks", "ERROR") as logs:
            result = send_assignment_due_reminder(str(self.assignment.id), 24)

        self.assert_class_only(logs, "due reminder email for teacher")
        self.assertEqual(result, "Queued 0 assignment due reminder emails.")

    def test_a_failed_student_reminder_is_logged_by_class_only(self):
        """S4 (send_assignment_due_reminder, a student's send)."""
        self.notify(self.teacher, notify_assignment_due_reminder=False)
        self.notify(self.student, notify_assignment_due_reminder=True)

        with patch(
            "assignments.tasks.send_email_task.delay",
            side_effect=failing_with_the_marker(),
        ), self.assertLogs("assignments.tasks", "ERROR") as logs:
            result = send_assignment_due_reminder(str(self.assignment.id), 24)

        self.assert_class_only(logs, "due reminder email for student")
        self.assertEqual(result, "Queued 0 assignment due reminder emails.")

    def test_a_failed_new_assignment_notice_is_logged_by_class_only(self):
        """S5 (send_new_assignment_posted_notification)."""
        self.notify(self.student, notify_new_assignment_posted=True)

        with patch(
            "assignments.tasks.send_email_task.delay",
            side_effect=failing_with_the_marker(),
        ), self.assertLogs("assignments.tasks", "ERROR") as logs:
            result = send_new_assignment_posted_notification(str(self.assignment.id))

        self.assert_class_only(logs, "new assignment notification")
        self.assertEqual(result, "Queued 0 new assignment notification email(s).")

    def test_a_reminder_that_sends_logs_no_error(self):
        """K1 (control): nothing fails, one send, no ERROR line."""
        self.notify(self.teacher, notify_assignment_due_reminder=True)
        self.notify(self.student, notify_assignment_due_reminder=False)

        with patch("assignments.tasks.send_email_task.delay") as send:
            with self.assertNoLogs("assignments.tasks", "ERROR"):
                result = send_assignment_due_reminder(str(self.assignment.id), 24)

        self.assertEqual(result, "Queued 1 assignment due reminder emails.")
        send.assert_called_once()


class PrerenderLogLinesCarryNoTextTest(
    ClassOnlyLogAssertions, RigorFixtureMixin, TestCase
):

    def setUp(self):
        cache.clear()
        pdf_cache._inflight.clear()
        self.addCleanup(cache.clear)
        self.addCleanup(pdf_cache._inflight.clear)
        self.assignment = Assignment.objects.create(
            title="H-219 Render",
            course=self.make_course(suffix="-h219"),
            status=AssignmentStatus.PUBLISHED,
            total_points=5,
            questions=[objective_question()],
        )

    def test_a_failed_pre_render_is_logged_by_class_only(self):
        """S6 (prerender_assignment_pdfs): both views fail, each one line."""
        with patch(
            "assignments.pdf_document.render_assignment_pdf",
            side_effect=failing_with_the_marker(),
        ), self.assertLogs("assignments.tasks", "ERROR") as logs:
            result = prerender_assignment_pdfs(str(self.assignment.id))

        self.assert_class_only(logs, "(student view)")
        self.assert_class_only(logs, "(teacher view)")
        self.assertIn("nothing", result)
