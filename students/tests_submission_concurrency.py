"""
Concurrency coverage for the submission write paths in students.services /
students.models that the section 7 audit found unguarded.

Each test here reproduces the race with real threads on real DB
connections (TransactionTestCase - a TestCase's single wrapping
transaction would serialise everything and hide the race), synchronised
with a barrier placed exactly where the two writers used to interleave.
Reverting the corresponding fix turns each test red; that mutation check
is what makes them regression locks rather than exercises.

Run with:
    python manage.py test students.tests_submission_concurrency
"""

import threading
from datetime import timedelta
from unittest.mock import patch

from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from assignments.models import Assignment
from classrooms.models import Course, Session
from students import services
from students.exceptions import SubmissionLimitReachedError
from students.models import (
    BatchUploadSession,
    BatchUploadType,
    GradingState,
    StudentSubmission,
)
from students.services import (
    MAX_STUDENT_SUBMISSION_ATTEMPTS,
    grade_engine,
    upload_answers_engine,
)
from users.models import CustomUser, UserTypes

# Long enough that a thread blocked on a row lock is unmistakably blocked,
# short enough that the fixed code (where the barrier can never complete)
# doesn't make the suite crawl.
RENDEZVOUS_TIMEOUT = 2


def _rendezvous(barrier):
    """Wait for the other writer; a timeout means it's (correctly) locked out."""
    try:
        barrier.wait(timeout=RENDEZVOUS_TIMEOUT)
    except threading.BrokenBarrierError:
        pass


def _make_course(tag):
    teacher = CustomUser.objects.create_user(
        email=f"{tag}-teacher@example.com",
        password="password123",  # pragma: allowlist secret
        user_type=UserTypes.TEACHER,
        first_name="Terry",
        last_name="Teacher",
    )
    session = Session.objects.create(name="S", teacher=teacher)
    course = Course.objects.create(name="C", teacher=teacher, session=session)
    assignment = Assignment.objects.create(
        title="A",
        course=course,
        questions=[{"question_number": 1, "question_text": "Q1?", "points": 10}],
    )
    return teacher, course, assignment


def _make_student(tag):
    return CustomUser.objects.create_user(
        email=f"{tag}-student@example.com",
        password="password123",  # pragma: allowlist secret
        user_type=UserTypes.STUDENT,
        first_name="Sally",
        last_name="Student",
    )


class BatchUploadSessionResultsConcurrencyTest(TransactionTestCase):
    """
    A "Grade All" / batch upload fans out one Celery task per file and every
    one of them appends to the same BatchUploadSession.results JSON list via
    update_result(). That is a read-modify-write, and it ran with no row
    lock: two workers read the same list, each appended their own entry,
    and the second save silently overwrote the first - the batch reported
    fewer results than files, forever.
    """

    def test_concurrent_update_result_calls_lose_no_entries(self):
        teacher, _, _ = _make_course("batch-race")
        session = BatchUploadSession.objects.create(
            teacher=teacher, task_type=BatchUploadType.GRADE, total_files=2
        )

        # Both writers must have READ the list before either SAVES. With the
        # row lock in place the second reader can't get in until the first
        # has committed, so the barrier times out - which is the point.
        save_barrier = threading.Barrier(2)
        original_save = BatchUploadSession.save

        def save_after_rendezvous(instance, *args, **kwargs):
            _rendezvous(save_barrier)
            return original_save(instance, *args, **kwargs)

        errors = []

        def worker(index):
            try:
                session.update_result(
                    f"file-{index}", "SUCCESS", batch_type=BatchUploadType.GRADE
                )
            except Exception as exc:  # pragma: no cover - surfaced by assert
                errors.append(exc)
            finally:
                connection.close()

        with patch.object(BatchUploadSession, "save", save_after_rendezvous):
            threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=15)

        self.assertEqual(errors, [])
        session.refresh_from_db()
        self.assertEqual(
            sorted(entry["file_name"] for entry in session.results),
            ["file-0", "file-1"],
            "one worker's batch result was overwritten by the other's",
        )


class SubmissionAttemptLimitConcurrencyTest(TransactionTestCase):
    """
    upload_answers_engine locks the student's submission row to enforce the
    per-assignment attempt limit - but the lock was released BEFORE the
    incremented count was saved (the save sat outside the atomic block).
    Two concurrent uploads by a student on their last allowed attempt
    therefore both read the old count, both passed the guard, and the
    limit was bypassed. The save now happens under the lock.
    """

    def test_concurrent_uploads_cannot_bypass_the_attempt_limit(self):
        _, _, assignment = _make_course("limit-race")
        student = _make_student("limit-race")
        submission = StudentSubmission.objects.create(
            assignment=assignment,
            student=student,
            answers=[{"question_number": 1, "answer_html": "old"}],
            attempt_count=MAX_STUDENT_SUBMISSION_ATTEMPTS - 1,
        )
        extracted = {
            "answers": [{"question_number": 1, "answer_html": "new"}],
            "extraction_confidence": 90,
        }

        # Both uploads must have passed the guard and reached the render step
        # before either saves. Under the fixed code the second upload can't
        # reach the render step until the first has committed.
        render_barrier = threading.Barrier(2)
        original_render = services.student_submission_to_html

        def render_after_rendezvous(instance):
            _rendezvous(render_barrier)
            return original_render(instance)

        outcomes = []
        errors = []

        def worker():
            try:
                upload_answers_engine(assignment, "ignored", student)
                outcomes.append("accepted")
            except SubmissionLimitReachedError:
                outcomes.append("rejected")
            except Exception as exc:  # pragma: no cover - surfaced by assert
                errors.append(exc)
            finally:
                connection.close()

        with patch("students.services.ai_processor") as mock_ai, patch(
            "students.services.student_submission_to_html", render_after_rendezvous
        ):
            mock_ai.extract_answer_with_retry.return_value = extracted
            threads = [threading.Thread(target=worker) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=15)

        self.assertEqual(errors, [])
        self.assertEqual(
            sorted(outcomes),
            ["accepted", "rejected"],
            "both uploads were accepted - the attempt limit was bypassed",
        )
        submission.refresh_from_db()
        self.assertEqual(submission.attempt_count, MAX_STUDENT_SUBMISSION_ATTEMPTS)


class GradingSaveDoesNotClobberConcurrentWritesTest(TestCase):
    """
    A grading run takes minutes and its StudentSubmission instance was
    loaded before it started. The final save used to be a full-row save,
    which wrote every column back from that stale instance - so anything
    that landed on the row during the run (a student's re-upload, a publish)
    was silently reverted. The final save is now limited to the columns the
    grading pipeline owns.
    """

    def test_columns_changed_during_the_run_survive_the_final_save(self):
        teacher, _, assignment = _make_course("clobber")
        student = _make_student("clobber")
        submission = StudentSubmission.objects.create(
            assignment=assignment,
            student=student,
            answers=[{"question_number": 1, "answer_html": "original"}],
            attempt_count=1,
        )
        new_answers = [{"question_number": 1, "answer_html": "re-uploaded"}]

        def grade_while_someone_else_writes(*args, **kwargs):
            # A re-upload and a publish landing mid-run, exactly as they
            # would from another worker / request.
            StudentSubmission.objects.filter(pk=submission.pk).update(
                answers=new_answers, attempt_count=2, is_published=True
            )
            return {
                "grading_summary": {
                    "total_score": 8,
                    "max_total_points": 10,
                    "percentage": 80.0,
                },
                "grading_confidence": 90,
                "question_evaluations": [],
            }

        with patch("students.services.ai_processor") as mock_ai:
            mock_ai.extract_grade_with_retry.side_effect = (
                grade_while_someone_else_writes
            )
            grade_engine(teacher, submission)

        submission.refresh_from_db()
        # The grade landed...
        self.assertEqual(submission.grading_state, GradingState.DONE)
        self.assertEqual(float(submission.score), 8.0)
        self.assertIsNotNone(submission.graded_at)
        # ...and the concurrent writes were not reverted by it.
        self.assertEqual(submission.answers, new_answers)
        self.assertEqual(submission.attempt_count, 2)
        self.assertTrue(submission.is_published)


class ResubmissionDoesNotClobberOtherColumnsTest(TestCase):
    """The re-submission path writes only the columns it owns; everything
    else on the row is left exactly as it was. (A GRADED row can no longer
    be re-submitted at all - the post-grading product rule, proven in
    tests_post_grading_submission_lock - so the columns exercised here are
    the ones an UNGRADED row can carry: a scheduled grading, the formatter's
    output, a manual-regrade flag.)"""

    def test_reupload_keeps_the_other_columns_intact(self):
        _, _, assignment = _make_course("resubmit")
        student = _make_student("resubmit")
        scheduled_at = timezone.now() + timedelta(hours=1)
        submission = StudentSubmission.objects.create(
            assignment=assignment,
            student=student,
            answers=[{"question_number": 1, "answer_html": "old"}],
            attempt_count=1,
            formatted_grade="kept",
            scheduled_grading_at=scheduled_at,
            grading_task_name="grade-submission-kept",
            was_regraded=True,
            is_published=True,
        )

        with patch("students.services.ai_processor") as mock_ai:
            mock_ai.extract_answer_with_retry.return_value = {
                "answers": [{"question_number": 1, "answer_html": "new"}],
                "extraction_confidence": 75,
            }
            upload_answers_engine(assignment, "ignored", student)

        submission.refresh_from_db()
        self.assertEqual(submission.answers[0]["answer_html"], "new")
        self.assertEqual(submission.attempt_count, 2)
        self.assertEqual(submission.extraction_confidence, 75)
        self.assertEqual(submission.formatted_grade, "kept")
        self.assertEqual(submission.scheduled_grading_at, scheduled_at)
        self.assertEqual(submission.grading_task_name, "grade-submission-kept")
        self.assertTrue(submission.was_regraded)
        self.assertTrue(submission.is_published)
        self.assertIsNone(submission.graded_at)
