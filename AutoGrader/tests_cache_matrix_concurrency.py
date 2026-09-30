"""H-1 Stage 3: concurrency gate.

Real threads, real Postgres connections, real Redis - proves the new
targeted-invalidation receivers (G1-G9, P1-P5) don't lose a bump or leave
a permanently stale read under concurrent load, the way the plan's gate
table requires ("barrier-synchronised concurrent mutations ... lose no
bump and leave no stale read").

Two scenarios:

* `PublishAllGradesConcurrencyTests`: the real staleness risk behind G3 -
  a teacher's `publish-all-grades` (assignments/views.py) racing a burst
  of students reading their own submission list through the real
  endpoint. Every student's FINAL read, after the burst settles, must
  show the published result - none may be left permanently stale by a
  lost bump.
* `ConcurrentEnrollmentConcurrencyTests`: the real staleness risk behind
  G5 - many students enrolling into the same course at once (each a
  separate `StudentCourse` row, each firing `clear_student_course_cache`,
  each calling `_course_scopes`, which now queries and re-bumps every
  OTHER enrolled student). Every enrolled student's cached course
  retrieve must end up FRESH once the burst settles, and the teacher's
  final student count must equal exactly how many actually committed -
  no bump lost to a lost race.

Each worker closes its own DB connection on exit (Django opens one per
thread; a concurrency test that leaks them would exhaust the server
rather than prove anything about it).

Real Redis + real Postgres.
"""

import threading

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connections
from django.test import TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from assignments.models import Assignment, AssignmentStatus
from classrooms.models import Course, EnrollmentStatusType, Session, StudentCourse
from students.models import StudentSubmission
from users.models import UserTypes

User = get_user_model()


def make_active_user(email, user_type, first_name):
    return User.objects.create_user(
        email=email,
        password="password123",  # nosec  # pragma: allowlist secret
        user_type=user_type,
        is_active=True,
        first_name=first_name,
        last_name=user_type.title(),
    )


def question(number=1):
    return {
        "question_number": number,
        "question_text": f"Q{number}",
        "question_type": "OBJECTIVE",
        "points": 10,
        "options": ["one", "two"],
        "rubric": [],
        "model_answer": "one",
    }


def _run_barriered(workers, timeout=60):
    """Release every worker at once; each closes its own DB connection on
    the way out. Fails loudly if any thread is still alive after `timeout`
    - a wedged worker is a correctness bug, not something to wait out."""
    barrier = threading.Barrier(len(workers))
    errors: list = [None] * len(workers)

    def wrapped(index, fn):
        try:
            barrier.wait(timeout=30)
            fn()
        except BaseException as exc:  # noqa: BLE001 - surfaced, not silent
            errors[index] = exc
        finally:
            connections.close_all()

    threads = [
        threading.Thread(target=wrapped, args=(i, fn)) for i, fn in enumerate(workers)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=timeout)
    stuck = [t for t in threads if t.is_alive()]
    assert not stuck, f"{len(stuck)} worker(s) never finished - the system is wedged"
    real_errors = [e for e in errors if e is not None]
    assert not real_errors, f"{len(real_errors)} worker(s) raised: {real_errors!r}"


class PublishAllGradesConcurrencyTests(TransactionTestCase):
    """G3 under concurrency: publish-all racing a burst of student reads."""

    reset_sequences = True

    def setUp(self):
        cache.clear()
        self.teacher = make_active_user(
            "conc-g3-teacher@x.test", UserTypes.TEACHER, "ConcG3T"
        )
        self.session = Session.objects.create(name="Conc term", teacher=self.teacher)
        self.course = Course.objects.create(
            name="Conc course", teacher=self.teacher, session=self.session
        )
        self.assignment = Assignment.objects.create(
            title="Conc assignment",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            questions=[question()],
        )

        self.student_count = 12
        self.students = []
        self.submissions = []
        for i in range(self.student_count):
            student = make_active_user(
                f"conc-g3-student-{i}@x.test", UserTypes.STUDENT, f"ConcG3S{i}"
            )
            StudentCourse.objects.create(
                student=student,
                course=self.course,
                enrollment_status=EnrollmentStatusType.ENROLLED,
            )
            submission = StudentSubmission.objects.create(
                assignment=self.assignment,
                student=student,
                answers=[{"question_number": 1, "answer_html": "one"}],
                graded_at=timezone.now(),
                score=8,
                is_published=False,
            )
            self.students.append(student)
            self.submissions.append(submission)

        self.list_url = reverse("student-submission-list")

    def _read_is_published(self, student):
        client = APIClient()
        client.force_authenticate(student)
        response = client.get(self.list_url)
        self.assertEqual(response.status_code, 200, response.content)
        results = response.data.get("results", response.data)
        return all(row["is_published"] for row in results) and len(results) == 1

    def test_publish_all_settles_fresh_for_every_student_under_concurrent_reads(self):
        # Every student warms a cache entry BEFORE the burst, showing
        # is_published=False - the state a lost bump would leave behind.
        for student in self.students:
            self.assertFalse(self._read_is_published(student))

        def publish_all():
            client = APIClient()
            client.force_authenticate(self.teacher)
            response = client.post(
                reverse("assignment-publish-all-grades", args=[self.assignment.pk])
            )
            assert response.status_code == 200, response.content

        def read_during_burst(student):
            def _read():
                client = APIClient()
                client.force_authenticate(student)
                client.get(self.list_url)

            return _read

        workers = [publish_all] + [
            read_during_burst(student) for student in self.students
        ]
        _run_barriered(workers)

        # The decisive assertion: AFTER the burst settles, every student's
        # NEXT read (a fresh cache entry, since threads may have warmed
        # racing intermediate states) must show the published result. No
        # student may be stuck on a lost bump.
        stale_students = [
            student.email
            for student in self.students
            if not self._read_is_published(student)
        ]
        self.assertEqual(
            stale_students,
            [],
            f"{len(stale_students)}/{self.student_count} students still see "
            "unpublished results after the concurrent publish-all settled",
        )


class ConcurrentEnrollmentConcurrencyTests(TransactionTestCase):
    """G5 under concurrency: many students enrolling into one course at once."""

    reset_sequences = True

    def setUp(self):
        cache.clear()
        self.teacher = make_active_user(
            "conc-g5-teacher@x.test", UserTypes.TEACHER, "ConcG5T"
        )
        self.session = Session.objects.create(name="Conc G5 term", teacher=self.teacher)
        self.course = Course.objects.create(
            name="Conc G5 course", teacher=self.teacher, session=self.session
        )

        self.student_count = 12
        self.students = [
            make_active_user(
                f"conc-g5-student-{i}@x.test", UserTypes.STUDENT, f"ConcG5S{i}"
            )
            for i in range(self.student_count)
        ]

        self.retrieve_url = reverse("course-detail", args=[self.course.pk])

    def _cached_student_count(self, viewer):
        client = APIClient()
        client.force_authenticate(viewer)
        response = client.get(self.retrieve_url)
        self.assertEqual(response.status_code, 200, response.content)
        return response.data["student_count"]

    def test_concurrent_enrollments_settle_fresh_with_no_lost_bump(self):
        # Warm every future viewer's cache at 0 students enrolled.
        self.assertEqual(self._cached_student_count(self.teacher), 0)

        def enroll_as(teacher, course, email):
            def _enroll():
                client = APIClient()
                client.force_authenticate(teacher)
                response = client.post(
                    reverse("course-students", args=[course.pk]), {"email": email}
                )
                assert response.status_code in (200, 201), response.content

            return _enroll

        workers = [
            enroll_as(self.teacher, self.course, student.email)
            for student in self.students
        ]
        _run_barriered(workers)

        actual_enrolled = StudentCourse.objects.filter(course=self.course).count()
        self.assertEqual(
            actual_enrolled,
            self.student_count,
            "a concurrent enrollment was lost at the database level - not a "
            "cache bug, but the precondition for one",
        )

        # The decisive assertion: the teacher's NEXT read reflects every
        # enrollment, not a stale count from a lost bump.
        self.assertEqual(
            self._cached_student_count(self.teacher),
            self.student_count,
            "the teacher's cached course retrieve lost a bump under "
            "concurrent enrollment and undercounts students",
        )
