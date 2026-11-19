"""GET tasks/sessions: a teacher's batch sessions (owner, 2026-11-18).

A teacher who logs out or switches device finds their batches again here,
then reads session-results or retries by session_id. Pinned:

* the caller's own sessions only, newest first, paginated; teachers only;
  a session whose course the teacher can no longer reach (H-38) is absent;
* each row's counts, and resumable / retryable_count by the same rule as
  session-results (item_retry's upload rule with kept files, the catalogue
  flag for other items);
* the filters ?resumable=, ?assignment_id=, ?task_type=;
* the query count does not grow with the page.

    python manage.py test users.tests_task_sessions_list
"""

import uuid
from datetime import timedelta
from typing import TYPE_CHECKING

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from assignments.models import Assignment, AssignmentStatus
from classrooms.models import Course, School, Session, SessionOwnerType
from students import kept_files
from students.models import (
    BackgroundProcessingTask,
    BackgroundTaskStatus,
    BackgroundTaskType,
    BatchUploadSession,
    BatchUploadType,
    KeptUpload,
)
from users.models import CustomUser, UserTypes

ANSWERS = BackgroundTaskType.BATCH_ANSWER_UPLOAD
GRADING = BackgroundTaskType.BATCH_SUBMISSION_GRADING
FAILURE = BackgroundTaskStatus.FAILURE


def user(tag, user_type=UserTypes.TEACHER, school=None):
    return CustomUser.objects.create_user(
        email=f"sessions-{tag}@example.com",
        password="password123",  # pragma: allowlist secret
        user_type=user_type,
        is_active=True,
        school=school,
    )


if TYPE_CHECKING:
    _Base = TestCase
else:
    # A plain mixin at run time, so the loader never collects it on its own.
    _Base = object


class SessionsFixture(_Base):
    def setUp(self):
        self.teacher = user("teacher")
        self.course = Course.objects.create(
            name="Maths",
            teacher=self.teacher,
            session=Session.objects.create(name="S", teacher=self.teacher),
        )
        self.assignment = Assignment.objects.create(
            title="Homework 1",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            questions=[{"question_number": 1, "question_text": "Q1?", "points": 10}],
        )
        self.api = APIClient()
        self.api.force_authenticate(user=self.teacher)
        self.index = 0

    def batch(
        self,
        *,
        kind=BatchUploadType.SUBMISSION,
        teacher=None,
        course=None,
        assignment="default",
        age_minutes=0,
    ):
        """A batch session; answer batches carry the assignment, the others
        the course (as the routes make them)."""
        if assignment == "default":
            assignment = self.assignment if kind == BatchUploadType.SUBMISSION else None
        session = BatchUploadSession.objects.create(
            teacher=teacher or self.teacher,
            task_type=kind,
            assignment=assignment,
            course=(
                course
                if course is not None
                else (None if assignment is not None else self.course)
            ),
            total_files=0,
        )
        BatchUploadSession.objects.filter(pk=session.pk).update(
            created_at=timezone.now() - timedelta(minutes=age_minutes)
        )
        session.refresh_from_db()
        return session

    def item(
        self,
        session,
        *,
        task_type=ANSWERS,
        status=FAILURE,
        code="",
        kept=False,
        purged=False,
    ):
        self.index += 1
        item = BackgroundProcessingTask.objects.create(
            requested_by=session.teacher,
            task_type=task_type,
            batch_session=session,
            assignment=session.assignment or self.assignment,
            file_name=f"f{self.index}.png",
            item_index=self.index,
            status=status,
            reason_code=code,
        )
        if kept:
            stored = kept_files.store(b"x")
            KeptUpload.objects.create(
                processing_task=item,
                storage_key=stored.storage_key,
                original_name=item.file_name or "",
                content_type="image/png",
                size_bytes=stored.size_bytes,
                sha256=stored.sha256,
                kept_until=timezone.now() + timedelta(days=30),
                purged_at=timezone.now() if purged else None,
                purge_reason=KeptUpload.PurgeReason.LIFETIME if purged else "",
            )
        return item

    def sessions(self, client=None, **params):
        response = (client or self.api).get(reverse("task-sessions"), params)
        self.assertEqual(response.status_code, 200, response.content[:400])
        return response.json()["data"]

    def row(self, data, session):
        return next(r for r in data["results"] if r["session_id"] == str(session.id))


class WhoSeesWhat(SessionsFixture, TestCase):
    def test_the_teacher_sees_only_their_own_sessions(self):
        mine = self.batch()
        other_teacher = user("other")
        other_course = Course.objects.create(name="Other", teacher=other_teacher)
        theirs = self.batch(
            kind=BatchUploadType.ASSIGNMENT,
            teacher=other_teacher,
            course=other_course,
            assignment=None,
        )

        data = self.sessions()
        self.assertEqual([r["session_id"] for r in data["results"]], [str(mine.id)])

        other_api = APIClient()
        other_api.force_authenticate(user=other_teacher)
        ids = [r["session_id"] for r in self.sessions(other_api)["results"]]
        self.assertEqual(ids, [str(theirs.id)])
        self.assertNotIn(str(mine.id), ids)

    def test_a_session_of_an_unreachable_course_is_absent(self):
        other_school = School.objects.create(name="Elsewhere")
        lost = Course.objects.create(
            name="Lost",
            teacher=self.teacher,
            session=Session.objects.create(
                name="L", owner_type=SessionOwnerType.SCHOOL, school=other_school
            ),
        )
        lost_assignment = Assignment.objects.create(
            title="Lost work", course=lost, status=AssignmentStatus.PUBLISHED
        )
        self.batch(assignment=lost_assignment)
        self.batch(kind=BatchUploadType.ASSIGNMENT, course=lost, assignment=None)
        kept = self.batch()

        data = self.sessions()
        self.assertEqual([r["session_id"] for r in data["results"]], [str(kept.id)])
        self.assertEqual(data["count"], 1)

    def test_a_student_is_refused(self):
        student_api = APIClient()
        student_api.force_authenticate(user=user("student", UserTypes.STUDENT))
        response = student_api.get(reverse("task-sessions"))
        self.assertEqual(response.status_code, 403, response.content[:400])

    def test_newest_first(self):
        old = self.batch(age_minutes=30)
        new = self.batch(age_minutes=1)
        middle = self.batch(age_minutes=10)
        ids = [r["session_id"] for r in self.sessions()["results"]]
        self.assertEqual(ids, [str(new.id), str(middle.id), str(old.id)])


class TheRow(SessionsFixture, TestCase):
    def test_the_fields_and_the_counts(self):
        session = self.batch()
        self.item(session, status=BackgroundTaskStatus.SUCCESS)
        self.item(session, status=BackgroundTaskStatus.SUCCESS)
        self.item(session, code="FILE_UNREADABLE")
        self.item(session, status=BackgroundTaskStatus.CANCELLED)
        self.item(session, status=BackgroundTaskStatus.PENDING)
        self.item(session, status=BackgroundTaskStatus.STARTED)

        row = self.row(self.sessions(), session)
        self.assertEqual(
            row,
            {
                "session_id": str(session.id),
                "task_type": BatchUploadType.SUBMISSION,
                "assignment_id": str(self.assignment.id),
                "assignment_title": "Homework 1",
                "course_id": str(self.course.id),
                "course_name": "Maths",
                "created_at": row["created_at"],
                "success_count": 2,
                "failure_count": 1,
                "cancelled_count": 1,
                "pending_count": 2,
                "dismissed_count": 0,
                "resumable": False,
                "retryable_count": 0,
            },
        )
        self.assertTrue(row["created_at"])

    def test_an_assignment_upload_reads_its_course_and_no_assignment(self):
        session = self.batch(kind=BatchUploadType.ASSIGNMENT, assignment=None)
        row = self.row(self.sessions(), session)
        self.assertIsNone(row["assignment_id"])
        self.assertIsNone(row["assignment_title"])
        self.assertEqual(row["course_id"], str(self.course.id))
        self.assertEqual(row["course_name"], "Maths")

    def test_resumable_and_retryable_count_follow_session_results(self):
        parked = self.batch()
        self.item(parked, code="INSUFFICIENT_CREDITS", kept=True)
        purged = self.batch()
        self.item(purged, code="INSUFFICIENT_CREDITS", kept=True, purged=True)
        grading = self.batch(kind=BatchUploadType.GRADE, assignment=None)
        self.item(grading, task_type=GRADING, code="PROVIDER_FAILURE")
        self.item(grading, task_type=GRADING, code="INSUFFICIENT_CREDITS")
        poor = self.batch(kind=BatchUploadType.GRADE, assignment=None)
        self.item(poor, task_type=GRADING, code="INSUFFICIENT_CREDITS")

        data = self.sessions()
        expected = {
            parked: (True, 1),
            purged: (False, 0),
            grading: (True, 1),
            poor: (False, 0),
        }
        for session, (resumable, count) in expected.items():
            row = self.row(data, session)
            self.assertEqual(
                (row["resumable"], row["retryable_count"]),
                (resumable, count),
                session.task_type,
            )
            results = self.api.get(
                reverse("task-session-results", kwargs={"session_id": str(session.id)})
            ).json()["data"]
            self.assertEqual(results["resumable"], resumable)
            self.assertEqual(
                sum(1 for e in results["failure_list"] if e["retryable"]), count
            )


class Filters(SessionsFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.resumable = self.batch(age_minutes=3)
        self.item(self.resumable, code="INSUFFICIENT_CREDITS", kept=True)
        self.done = self.batch(age_minutes=2)
        self.item(self.done, status=BackgroundTaskStatus.SUCCESS)
        self.other_assignment = Assignment.objects.create(
            title="Homework 2", course=self.course, status=AssignmentStatus.PUBLISHED
        )
        self.elsewhere = self.batch(assignment=self.other_assignment, age_minutes=1)
        self.grading = self.batch(kind=BatchUploadType.GRADE, assignment=None)

    def ids(self, **params):
        return [r["session_id"] for r in self.sessions(**params)["results"]]

    def test_resumable_true(self):
        self.assertEqual(self.ids(resumable="true"), [str(self.resumable.id)])

    def test_resumable_false(self):
        self.assertNotIn(str(self.resumable.id), self.ids(resumable="false"))
        self.assertEqual(len(self.ids(resumable="false")), 3)

    def test_assignment_id(self):
        self.assertEqual(
            self.ids(assignment_id=str(self.other_assignment.id)),
            [str(self.elsewhere.id)],
        )

    def test_an_unknown_or_malformed_assignment_id_matches_nothing(self):
        self.assertEqual(self.ids(assignment_id=str(uuid.uuid4())), [])
        self.assertEqual(self.ids(assignment_id="not-a-uuid"), [])

    def test_task_type(self):
        self.assertEqual(
            self.ids(task_type=BatchUploadType.GRADE), [str(self.grading.id)]
        )

    def test_task_type_in_any_case(self):
        # E2E F7: the values are lowercase; ?task_type=GRADE found nothing.
        for value in ("GRADE", "Grade", " grade "):
            self.assertEqual(self.ids(task_type=value), [str(self.grading.id)], value)

    def test_an_unknown_task_type_matches_nothing(self):
        # As an unknown assignment_id does (this list has no 400 for a filter).
        response = self.api.get(reverse("task-sessions"), {"task_type": "bogus"})
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertEqual(response.json()["data"]["results"], [])


class QueryCount(SessionsFixture, TestCase):
    def fill(self, count):
        for n in range(count):
            session = self.batch(age_minutes=n)
            self.item(session, code="INSUFFICIENT_CREDITS", kept=True)
            self.item(session, status=BackgroundTaskStatus.SUCCESS)
            self.item(session, task_type=GRADING, code="PROVIDER_FAILURE")

    def count_queries(self, **params):
        # A first request reads per-process caches (the user, permissions);
        # warm them so only this route's own queries are compared.
        self.sessions(**params)
        with CaptureQueriesContext(connection) as queries:
            data = self.sessions(**params)
        return len(queries.captured_queries), data

    def test_one_session_and_ten_sessions_cost_the_same(self):
        self.fill(1)
        one, data = self.count_queries()
        self.assertEqual(len(data["results"]), 1)
        self.fill(9)
        ten, data = self.count_queries()
        self.assertEqual(len(data["results"]), 10)
        self.assertEqual(one, ten)

    def test_and_with_the_resumable_filter(self):
        self.fill(1)
        one, _ = self.count_queries(resumable="true")
        self.fill(9)
        ten, data = self.count_queries(resumable="true")
        self.assertEqual(len(data["results"]), 10)
        self.assertEqual(one, ten)
