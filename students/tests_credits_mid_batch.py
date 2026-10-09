"""
FR-A-07 S7c: credits running out mid-batch (08a §4.5, §5).

* A credit refusal inside a batch where another item already started or
  finished is INSUFFICIENT_CREDITS_MID_BATCH ("Credits ran out after
  {completed} of {total} items. The finished items are saved."), not the
  plain pre-flight INSUFFICIENT_CREDITS, and it marks the session.
* Items still to run check that mark BEFORE any provider call and fail with
  the same code: never charged.
* session-results reports stopped_at_item (the first such item) and
  resumable.
* Resume: after a top-up, retry-failed with the code retries the grade items
  in place; the completed items are untouched. Upload items are
  re-uploaded (F4).
* No billing migration: the mark is a nullable column on the batch session.
"""

import io
import uuid
from types import SimpleNamespace
from typing import Any, Callable
from unittest.mock import patch

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from PIL import Image
from rest_framework.test import APIClient

import assignments.tasks as assignment_tasks
from assignments.models import Assignment, AssignmentStatus
from billing.errors import InsufficientCreditsError
from classrooms.models import Course, EnrollmentStatusType, Session, StudentCourse
from students import task_tracking
from students.models import (
    BackgroundProcessingTask,
    BackgroundTaskStatus,
    BackgroundTaskType,
    BatchUploadSession,
    BatchUploadType,
    StudentSubmission,
)
from users.models import CustomUser, UserTypes

MID = "INSUFFICIENT_CREDITS_MID_BATCH"


def png_bytes():
    buffer = io.BytesIO()
    Image.new("RGB", (160, 90), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def run_inline(task_callable, processing_task, *args, **kwargs):
    kwargs["processing_task_id"] = str(processing_task.id)
    celery_id = str(uuid.uuid4())
    task_tracking.attach_celery_task(processing_task.id, celery_id)
    task_callable.apply(args=args, kwargs=kwargs, task_id=celery_id)
    return SimpleNamespace(id=celery_id)


class MidBatchFixture:
    #: Supplied by the TestCase this mixin is combined with.
    addCleanup: Callable[..., Any]
    assertEqual: Callable[..., Any]

    def build(self, students=4):
        cache.clear()
        self.teacher = self.user("teacher", UserTypes.TEACHER, "Tess")
        course = Course.objects.create(
            name="Maths",
            teacher=self.teacher,
            session=Session.objects.create(name="S7c", teacher=self.teacher),
        )
        self.assignment = Assignment.objects.create(
            title="Homework",
            course=course,
            status=AssignmentStatus.PUBLISHED,
            questions=[
                {
                    "question_number": 1,
                    "question_text": "Q1?",
                    "points": 10,
                    # S6d's rubric gate needs a marking guide.
                    "model_answer": "4",
                }
            ],
        )
        self.students = []
        for i in range(students):
            student = self.user(f"s{i}", UserTypes.STUDENT, f"Pupil{i}")
            StudentCourse.objects.create(
                student=student,
                course=course,
                enrollment_status=EnrollmentStatusType.ENROLLED,
            )
            self.students.append(student)
        self.api = APIClient()
        self.api.force_authenticate(user=self.teacher)
        target = patch(
            "users.permissions.HasCreditBalance.has_permission", return_value=True
        )
        target.start()
        self.addCleanup(target.stop)

    def user(self, key, user_type, first):
        return CustomUser.objects.create_user(
            email=f"s7c-{key}@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=user_type,
            first_name=first,
            last_name="S7c",
        )

    def session_results(self, session_id):
        response = self.api.get(
            reverse("task-session-results", kwargs={"session_id": str(session_id)})
        )
        self.assertEqual(response.status_code, 200, response.content[:400])
        return response.json()["data"]


class GradingBatchFixture(MidBatchFixture):
    """A 4-item grading batch, with a fake grade_engine that runs out."""

    def setUp(self):
        self.build(students=4)
        self.session = BatchUploadSession.objects.create(
            teacher=self.teacher,
            course=self.assignment.course,
            task_type=BatchUploadType.GRADE,
            total_files=4,
        )
        self.items = []
        for index, student in enumerate(self.students, start=1):
            submission = StudentSubmission.objects.create(
                assignment=self.assignment,
                student=student,
                answers=[{"question_number": 1, "answer_html": "<p>4</p>"}],
            )
            self.items.append(
                task_tracking.create_processing_task(
                    requested_by=self.teacher,
                    task_type=BackgroundTaskType.BATCH_SUBMISSION_GRADING,
                    batch_session=self.session,
                    assignment=self.assignment,
                    submission=submission,
                    item_index=index,
                )
            )
        self.graded = []  # submission ids the (fake) provider graded

    def fake_grading(self, credits_for):
        """grade_engine that grades `credits_for` submissions, then refuses."""

        def grade(user, submission, processing_task_id=None):
            if len(self.graded) >= credits_for:
                raise InsufficientCreditsError("balance 0 < estimate 900")
            self.graded.append(submission.id)
            submission.score = 8
            submission.graded_at = timezone.now()
            submission.save(update_fields=["score", "graded_at"])
            return submission

        return patch("assignments.tasks.grade_engine", side_effect=grade)

    def run_item(self, item):
        assignment_tasks.grade_engine_async.apply(
            args=(str(self.teacher.id), str(item.submission_id)),
            kwargs={
                "batch_id": str(self.session.id),
                "processing_task_id": str(item.id),
            },
        )


class AGradingBatchThatRunsOutOfCredits(GradingBatchFixture, TestCase):
    def test_the_rest_of_the_batch_stops_uncharged_with_its_own_code(self):
        with self.fake_grading(credits_for=2) as grade:
            for item in self.items:
                self.run_item(item)

        # Item 4 never reached the provider: it was short-circuited.
        self.assertEqual(grade.call_count, 3)
        statuses = [BackgroundProcessingTask.objects.get(pk=i.pk) for i in self.items]
        self.assertEqual(
            [(t.status, t.reason_code) for t in statuses],
            [
                (BackgroundTaskStatus.SUCCESS, ""),
                (BackgroundTaskStatus.SUCCESS, ""),
                (BackgroundTaskStatus.FAILURE, MID),
                (BackgroundTaskStatus.FAILURE, MID),
            ],
        )
        for task in statuses[2:]:
            self.assertEqual(
                task.error,
                "Credits ran out after 2 of 4 items. The finished items are saved.",
            )
            self.assertNotIn("balance", task.error or "")
        self.session.refresh_from_db()
        self.assertIsNotNone(self.session.credits_exhausted_at)

        data = self.session_results(self.session.id)
        self.assertEqual(data["stopped_at_item"], 3)
        self.assertEqual(data["failure_codes"], {MID: 2})
        self.assertIs(data["resumable"], True)

    def test_a_first_item_refused_before_anything_ran_is_the_plain_refusal(self):
        with self.fake_grading(credits_for=0):
            self.run_item(self.items[0])

        first = BackgroundProcessingTask.objects.get(pk=self.items[0].pk)
        self.assertEqual(first.reason_code, "INSUFFICIENT_CREDITS")
        self.session.refresh_from_db()
        self.assertIsNone(self.session.credits_exhausted_at)
        self.assertIsNone(self.session_results(self.session.id)["stopped_at_item"])

    def test_resume_after_a_top_up_completes_the_rest_and_leaves_the_done_alone(self):
        with self.fake_grading(credits_for=2):
            for item in self.items:
                self.run_item(item)
        done = {
            i.pk: StudentSubmission.objects.get(pk=i.submission_id).graded_at
            for i in self.items[:2]
            if i.submission_id is not None
        }

        # Topped up: the provider grades again.
        self.graded.clear()
        with self.fake_grading(credits_for=10), patch(
            "students.item_retry.launch_processing_task", side_effect=run_inline
        ):
            response = self.api.post(
                reverse(
                    "task-retry-failed", kwargs={"session_id": str(self.session.id)}
                ),
                {"reason_codes": [MID]},
                format="json",
            )

        self.assertEqual(response.status_code, 202, response.content[:400])
        self.assertEqual(
            sorted(response.json()["data"]["retried"]),
            sorted(str(i.id) for i in self.items[2:]),
        )
        rows = [BackgroundProcessingTask.objects.get(pk=i.pk) for i in self.items]
        self.assertEqual([t.status for t in rows], [BackgroundTaskStatus.SUCCESS] * 4)
        self.assertEqual([t.retry_count for t in rows], [0, 0, 1, 1])
        for pk, graded_at in done.items():
            item = BackgroundProcessingTask.objects.get(pk=pk)
            assert item.submission_id is not None
            self.assertEqual(
                StudentSubmission.objects.get(pk=item.submission_id).graded_at,
                graded_at,
            )
        self.session.refresh_from_db()
        self.assertIsNone(self.session.credits_exhausted_at)
        self.assertIsNone(self.session_results(self.session.id)["stopped_at_item"])


class AnyRetryInAStoppedBatchResumesIt(GradingBatchFixture, TestCase):
    """v2's pre-read of S7c: the stop mark was cleared only by a retry of an
    INSUFFICIENT_CREDITS_MID_BATCH item, so any other failed item retried
    after a top-up (a PROVIDER_FAILURE, say) was stopped by the stale mark
    and relabelled a credits failure, spending a retry. Any retry that wins
    its claim now clears it; a real shortfall re-marks it before any
    charge."""

    def setUp(self):
        super().setUp()
        # Item 1 failed at the provider (as S6d codes it); then the batch
        # ran out: item 2 graded, item 3 refused, item 4 stopped.
        BackgroundProcessingTask.objects.filter(pk=self.items[0].pk).update(
            status=BackgroundTaskStatus.FAILURE,
            reason_code="PROVIDER_FAILURE",
            error="The grading service couldn't finish this item.",
            finished_at=timezone.now(),
        )
        with self.fake_grading(credits_for=1):
            for item in self.items[1:]:
                self.run_item(item)
        self.assertEqual(
            self.states(),
            [
                (BackgroundTaskStatus.FAILURE, "PROVIDER_FAILURE"),
                (BackgroundTaskStatus.SUCCESS, ""),
                (BackgroundTaskStatus.FAILURE, MID),
                (BackgroundTaskStatus.FAILURE, MID),
            ],
        )
        self.assertTrue(self.stopped())
        self.graded.clear()

    def states(self):
        return [
            (t.status, t.reason_code)
            for t in (BackgroundProcessingTask.objects.get(pk=i.pk) for i in self.items)
        ]

    def stopped(self):
        return BatchUploadSession.objects.filter(
            pk=self.session.pk, credits_exhausted_at__isnull=False
        ).exists()

    def inline(self):
        return patch(
            "students.item_retry.launch_processing_task", side_effect=run_inline
        )

    def retry_failed(self, body=None):
        return self.api.post(
            reverse("task-retry-failed", kwargs={"session_id": str(self.session.id)}),
            body or {},
            format="json",
        )

    def test_a_provider_failure_retried_after_a_top_up_is_graded(self):
        with self.fake_grading(credits_for=10), self.inline():
            response = self.api.post(
                reverse(
                    "task-retry-item",
                    kwargs={
                        "session_id": str(self.session.id),
                        "item_id": str(self.items[0].id),
                    },
                )
            )
        self.assertEqual(response.status_code, 202, response.content[:400])
        first = BackgroundProcessingTask.objects.get(pk=self.items[0].pk)
        self.assertEqual(
            (first.status, first.reason_code), (BackgroundTaskStatus.SUCCESS, "")
        )
        self.assertEqual(first.retry_count, 1)
        self.assertEqual(self.graded, [self.items[0].submission_id])
        self.assertFalse(self.stopped())

    def test_retry_failed_with_a_lower_index_provider_failure_first_grades_them_all(
        self,
    ):
        # Item 1 (PROVIDER_FAILURE) is launched before item 3 (MID_BATCH),
        # and inline here, as a fast worker would run it.
        with self.fake_grading(credits_for=10), self.inline():
            response = self.retry_failed()
        self.assertEqual(response.status_code, 202, response.content[:400])
        self.assertEqual(
            response.json()["data"]["retried"],
            [str(self.items[i].id) for i in (0, 2, 3)],
        )
        self.assertEqual(self.states(), [(BackgroundTaskStatus.SUCCESS, "")] * 4)
        self.assertEqual(
            [
                BackgroundProcessingTask.objects.get(pk=i.pk).retry_count
                for i in self.items
            ],
            [1, 0, 1, 1],
        )
        self.assertFalse(self.stopped())

    def test_a_real_shortfall_after_the_clear_re_marks_and_charges_nothing(self):
        # No top-up: every retried item is refused again.
        with self.fake_grading(credits_for=0) as grade, self.inline():
            response = self.retry_failed()
        self.assertEqual(response.status_code, 202, response.content[:400])
        self.assertEqual(self.graded, [])  # nothing graded, nothing charged
        self.assertEqual(grade.call_count, 3)  # each refused at the credit check
        self.assertEqual(
            self.states(),
            [
                (BackgroundTaskStatus.FAILURE, MID),
                (BackgroundTaskStatus.SUCCESS, ""),
                (BackgroundTaskStatus.FAILURE, MID),
                (BackgroundTaskStatus.FAILURE, MID),
            ],
        )
        self.assertTrue(self.stopped(), "a real shortfall must stop the batch again")
        self.assertEqual(self.session_results(self.session.id)["stopped_at_item"], 1)

    def test_a_losing_claim_leaves_the_mark(self):
        from students.item_retry import ItemNotRetryable, retry_item

        stale = BackgroundProcessingTask.objects.get(pk=self.items[0].pk)
        # Another retry claimed and finished it in between.
        BackgroundProcessingTask.objects.filter(pk=stale.pk).update(
            retry_count=stale.retry_count + 1
        )
        with self.inline(), self.assertRaises(ItemNotRetryable):
            retry_item(stale, self.teacher)
        self.assertTrue(self.stopped())


class AnUploadBatchThatRunsOutOfCredits(MidBatchFixture, TestCase):
    def setUp(self):
        self.build(students=3)
        for target in (
            patch("students.views.launch_processing_task", side_effect=run_inline),
            # H-180's door asks the wallet before the session exists; this
            # class is about the balance running out AFTER the batch was
            # accepted (the extraction is made to raise), and the door has
            # its own tests (students.tests_upload_credit_door).
            patch("students.views.upload_refusal_if_unaffordable", return_value=None),
        ):
            target.start()
            self.addCleanup(target.stop)

    def test_the_rest_stops_uncharged_and_resumes_by_re_upload(self):
        calls = []

        def extract(*args, **kwargs):
            calls.append(1)
            if len(calls) > 1:
                raise InsufficientCreditsError("balance 0 < estimate 900")
            return {
                "student_name": "Pupil0 S7c",
                "answers": [{"question_number": 1, "answer_html": "<p>4</p>"}],
            }

        files = [
            SimpleUploadedFile(f"p{i}.png", png_bytes(), content_type="image/png")
            for i in (1, 2, 3)
        ]
        with patch(
            "students.services.ai_processor.extract_answer_with_retry",
            side_effect=extract,
        ):
            response = self.api.post(
                reverse(
                    "student-submission-batch-upload",
                    kwargs={"assignment_id": str(self.assignment.id)},
                ),
                {"answers": files},
                format="multipart",
            )

        self.assertEqual(response.status_code, 202, response.content[:400])
        # p3 never reached the extraction: short-circuited, uncharged.
        self.assertEqual(len(calls), 2)
        session_id = response.json()["data"]["session_id"]
        data = self.session_results(session_id)
        self.assertEqual(data["success_count"], 1)
        self.assertEqual(
            [(e["item_index"], e["reason_code"]) for e in data["failure_list"]],
            [(2, MID), (3, MID)],
        )
        self.assertEqual(data["stopped_at_item"], 2)

        # F4: an upload item is re-uploaded, never retried in place.
        failed = data["failure_list"][0]
        retry = self.api.post(
            reverse(
                "task-retry-item",
                kwargs={"session_id": session_id, "item_id": failed["item_id"]},
            )
        )
        self.assertEqual(retry.status_code, 409)
        self.assertEqual(
            retry.json()["error"]["field_errors"]["params"]["resolution"],
            "replace_file",
        )
