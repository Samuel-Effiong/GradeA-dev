"""Concurrency and failure-mode tests for the enrollment write paths.

These use TransactionTestCase and REAL threads against Postgres, not
mocked concurrency: each thread opens its own connection and commits, and a
barrier lines them up so they genuinely contend. The read-only tenancy
tests elsewhere cannot catch a lost update or a duplicate row - only
actually racing the writes can.
"""

import threading
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connections
from django.test import TransactionTestCase, override_settings
from django.urls import reverse
from kombu.exceptions import OperationalError
from rest_framework import status
from rest_framework.test import APIClient

from classrooms import signals
from classrooms.models import (
    Course,
    EnrollmentStatusType,
    School,
    Session,
    StudentCourse,
)
from classrooms.services import EnrollmentError, enroll_student_by_email
from users.models import UserTypes

User = get_user_model()


class ThreadSafeTransactionTestCase(TransactionTestCase):
    """TransactionTestCase that guarantees no connection outlives the test.

    Threads opened here get their own DB connection, and Django drops the
    test database at the end of the run - any connection still open makes
    that DROP fail with "database is being accessed by other users", which
    surfaces as a non-zero exit even when every test passed. Closing in
    tearDown as well as in each worker makes that impossible.
    """

    reset_sequences = True

    def tearDown(self):
        connections.close_all()
        super().tearDown()


def make_user(email, user_type, school=None, **fields):
    user = User.objects.create_user(email=email, password="password123")  # nosec
    user.user_type = user_type
    user.is_active = True
    user.school = school
    for name, value in fields.items():
        setattr(user, name, value)
    user.save()
    return user


class EnrollmentConcurrencyTests(ThreadSafeTransactionTestCase):
    """Racing writes against the same course and student."""

    def setUp(self):
        self.school = School.objects.create(name="Race School")
        self.teacher = make_user("race-t@x.test", UserTypes.TEACHER, self.school)
        self.session = Session.objects.create(name="Race", teacher=self.teacher)
        self.course = Course.objects.create(
            name="Race 101", teacher=self.teacher, session=self.session
        )

    def _run_concurrently(self, target, count):
        """Run `target(index)` in `count` threads released simultaneously."""
        barrier = threading.Barrier(count)
        results: list[tuple[str, object] | None] = [None] * count

        def wrapped(index):
            try:
                barrier.wait(timeout=30)
                results[index] = ("ok", target(index))
            except Exception as exc:  # noqa: BLE001 - recorded, asserted below
                results[index] = ("error", exc)
            finally:
                # Each thread gets its own connection; leaking them across a
                # TransactionTestCase leaves the test DB un-droppable.
                connections.close_all()

        threads = [threading.Thread(target=wrapped, args=(i,)) for i in range(count)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        return results

    def test_racing_enrollments_of_the_same_email_create_exactly_one_row(self):
        """Two teachers hitting "add student" at the same moment.

        The unique constraint on (student, course) is the backstop; what
        must not happen is two rows, or a crash that leaves a half-created
        student with no enrollment.
        """

        def enroll(_index):
            return enroll_student_by_email(course=self.course, email="racer@x.test")

        results = self._run_concurrently(enroll, 4)

        self.assertEqual(
            StudentCourse.objects.filter(course=self.course).count(),
            1,
            "concurrent enrollment produced duplicate rows",
        )
        self.assertEqual(User.objects.filter(email="racer@x.test").count(), 1)
        succeeded = [r for r in results if r and r[0] == "ok"]
        self.assertGreaterEqual(len(succeeded), 1, "every racing attempt failed")

    def test_a_losing_racer_fails_cleanly_not_with_a_server_error(self):
        """The losers must surface a recognisable error, not an opaque one.

        EnrollmentError and IntegrityError are both acceptable outcomes;
        anything else means the race escapes as a 500.
        """
        from django.db.utils import IntegrityError

        def enroll(_index):
            return enroll_student_by_email(course=self.course, email="racer2@x.test")

        results = self._run_concurrently(enroll, 4)

        for outcome, value in (r for r in results if r):
            if outcome == "error":
                self.assertIsInstance(
                    value,
                    (EnrollmentError, IntegrityError),
                    f"race surfaced an unexpected {type(value).__name__}: {value}",
                )

    def test_concurrent_enrollment_and_removal_leaves_consistent_state(self):
        student = make_user("flip@x.test", UserTypes.STUDENT, self.school)
        StudentCourse.objects.create(
            student=student,
            course=self.course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )

        from classrooms.services import remove_student_from_course

        def churn(index):
            if index % 2 == 0:
                try:
                    return remove_student_from_course(
                        course=self.course, student_id=student.id
                    )
                except EnrollmentError:
                    return "already gone"
            try:
                return enroll_student_by_email(course=self.course, email="flip@x.test")
            except EnrollmentError:
                return "already there"

        self._run_concurrently(churn, 6)

        # Whatever order they landed in, the invariant is the same: never
        # more than one enrollment row for this pair, and the student's
        # account is untouched.
        self.assertLessEqual(
            StudentCourse.objects.filter(course=self.course, student=student).count(),
            1,
        )
        self.assertTrue(User.objects.filter(pk=student.pk).exists())

    def test_concurrent_bulk_imports_do_not_duplicate_students(self):
        """Two teachers pasting the same roster into the same course."""
        from classrooms.services import import_roster, parse_roster

        rows, total = parse_roster(raw_data="Dup,Licate,dup@x.test")

        def do_import(_index):
            return import_roster(course=self.course, rows=rows, total_processed=total)

        self._run_concurrently(do_import, 4)

        self.assertEqual(User.objects.filter(email="dup@x.test").count(), 1)
        self.assertEqual(StudentCourse.objects.filter(course=self.course).count(), 1)


class BrokerOutageResilienceTests(ThreadSafeTransactionTestCase):
    """A Redis outage must not fail the teacher's action.

    Every roster notification goes through `safe_delay`, whose whole purpose
    is that a lost email never rolls back an enrollment. These prove the
    wiring actually routes that way - a bare `.delay()` would surface the
    broker error as a 500 and lose the enrollment with it.
    """

    def setUp(self):
        self.school = School.objects.create(name="Outage School")
        self.teacher = make_user("outage-t@x.test", UserTypes.TEACHER, self.school)
        self.session = Session.objects.create(name="Outage", teacher=self.teacher)
        self.course = Course.objects.create(
            name="Outage 101", teacher=self.teacher, session=self.session
        )
        self.client = APIClient()
        self.client.force_authenticate(self.teacher)

    def _broker_down(self):
        return patch(
            "classrooms.services.notifications.send_email_task.delay",
            side_effect=OperationalError("broker unreachable"),
        )

    def test_single_enrollment_survives_a_broker_outage(self):
        with self._broker_down():
            response = self.client.post(
                reverse("course-students", kwargs={"pk": self.course.id}),
                {"email": "outage-student@x.test"},
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(
            StudentCourse.objects.filter(course=self.course).exists(),
            "a lost notification rolled back the enrollment",
        )

    def test_bulk_import_survives_a_broker_outage(self):
        with self._broker_down():
            response = self.client.post(
                reverse("course-bulk-add-students", kwargs={"pk": self.course.id}),
                {"raw_data": "Bulk,Outage,bulk-outage@x.test"},
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["success_count"], 1)
        self.assertTrue(StudentCourse.objects.filter(course=self.course).exists())

    def test_removal_survives_a_broker_outage(self):
        student = make_user("removeme@x.test", UserTypes.STUDENT, self.school)
        StudentCourse.objects.create(
            student=student,
            course=self.course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )

        with self._broker_down():
            response = self.client.delete(
                reverse(
                    "course-remove-student",
                    kwargs={"pk": self.course.id, "student_id": student.id},
                )
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(
            StudentCourse.objects.filter(course=self.course, student=student).exists()
        )
        self.assertTrue(User.objects.filter(pk=student.pk).exists())


class CacheOutageResilienceTests(ThreadSafeTransactionTestCase):
    """A Redis outage must not fail an enrollment either.

    Cache invalidation runs in post_save/post_delete receivers, which Django
    executes inside the caller's transaction. Before this was handled, a
    raising `delete_pattern` propagated out of the receiver and took the
    write down with it - so a Redis blip made enrolling a student
    impossible, despite enrollment needing nothing from Redis.
    """

    def setUp(self):
        self.school = School.objects.create(name="Cache Outage School")
        self.teacher = make_user("cache-t@x.test", UserTypes.TEACHER, self.school)
        self.session = Session.objects.create(name="Cache", teacher=self.teacher)
        self.course = Course.objects.create(
            name="Cache 101", teacher=self.teacher, session=self.session
        )
        self.student = make_user("cache-s@x.test", UserTypes.STUDENT, self.school)

    def _cache_down(self):
        from redis.exceptions import ConnectionError as RedisConnectionError

        return patch(
            "django.core.cache.cache.delete_pattern",
            create=True,
            side_effect=RedisConnectionError("redis unreachable"),
        )

    def test_enrollment_survives_a_cache_outage(self):
        with self._cache_down():
            StudentCourse.objects.create(
                student=self.student,
                course=self.course,
                enrollment_status=EnrollmentStatusType.ENROLLED,
            )

        self.assertTrue(
            StudentCourse.objects.filter(
                student=self.student, course=self.course
            ).exists(),
            "a cache-invalidation failure rolled back the enrollment",
        )

    def test_withdrawal_survives_a_cache_outage(self):
        enrollment = StudentCourse.objects.create(
            student=self.student,
            course=self.course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )

        with self._cache_down():
            enrollment.withdrawn()

        enrollment.refresh_from_db()
        self.assertEqual(enrollment.enrollment_status, EnrollmentStatusType.WITHDRAWN)

    def test_course_creation_survives_a_cache_outage(self):
        with self._cache_down():
            course = Course.objects.create(
                name="Made during an outage",
                teacher=self.teacher,
                session=self.session,
            )

        self.assertTrue(Course.objects.filter(pk=course.pk).exists())

    def test_the_failure_is_logged_at_error_not_swallowed(self):
        """Stale-but-served is the deliberate trade; going unnoticed is not.

        The log line is the only signal that a revocation may not have taken
        effect, so it has to be alertable.
        """
        with self._cache_down():
            with self.assertLogs("classrooms.signals", level="ERROR") as captured:
                StudentCourse.objects.create(
                    student=self.student,
                    course=self.course,
                    enrollment_status=EnrollmentStatusType.ENROLLED,
                )

        self.assertTrue(
            any("stale" in line.lower() for line in captured.output),
            "the outage was swallowed without warning that reads may be stale",
        )

    def test_a_genuine_bug_still_raises(self):
        """Only unreachability is tolerated. A TypeError from a bad call is
        a defect and must not be hidden behind the outage handler."""
        with patch(
            "django.core.cache.cache.delete_pattern",
            create=True,
            side_effect=TypeError("bad pattern"),
        ):
            with self.assertRaises(TypeError):
                Course.objects.create(
                    name="Bug surfaces",
                    teacher=self.teacher,
                    session=self.session,
                )


class CacheOutageAcrossAppsTests(ThreadSafeTransactionTestCase):
    """The same principle, applied where the same pattern exists.

    `students/signals.py` and `users/signals.py` had the identical shape -
    an unguarded `cache.delete_pattern` inside a post_save receiver - so a
    Redis blip failed a submission save or a user save outright. Both now
    route through `AutoGrader.cache_utils.delete_cache_patterns`, the
    project's existing best-effort helper.

    These live here rather than in those apps' own suites because they are
    verifying one cross-cutting invariant: a cache failure must never lose
    a committed database write.
    """

    def _cache_down(self):
        from redis.exceptions import ConnectionError as RedisConnectionError

        return patch(
            "django.core.cache.cache.delete_pattern",
            create=True,
            side_effect=RedisConnectionError("redis unreachable"),
        )

    def test_saving_a_user_survives_a_cache_outage(self):
        with self._cache_down():
            user = make_user("outage-user@x.test", UserTypes.TEACHER)

        self.assertTrue(User.objects.filter(pk=user.pk).exists())

    def test_updating_a_user_survives_a_cache_outage(self):
        user = make_user("outage-user2@x.test", UserTypes.TEACHER)

        with self._cache_down():
            user.first_name = "Renamed"
            user.save(update_fields=["first_name"])

        user.refresh_from_db()
        self.assertEqual(user.first_name, "Renamed")

    def test_saving_a_submission_survives_a_cache_outage(self):
        from assignments.models import Assignment
        from students.models import StudentSubmission

        school = School.objects.create(name="Cross App School")
        teacher = make_user("xapp-t@x.test", UserTypes.TEACHER, school)
        student = make_user("xapp-s@x.test", UserTypes.STUDENT, school)
        session = Session.objects.create(name="X", teacher=teacher)
        course = Course.objects.create(name="X", teacher=teacher, session=session)
        StudentCourse.objects.create(
            student=student,
            course=course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )
        assignment = Assignment.objects.create(
            title="X", course=course, teacher=teacher
        )

        with self._cache_down():
            submission = StudentSubmission.objects.create(
                student=student, assignment=assignment, answers={}
            )

        self.assertTrue(StudentSubmission.objects.filter(pk=submission.pk).exists())


class MissingCachePatternSupportTests(ThreadSafeTransactionTestCase):
    """A backend without `delete_pattern` disables invalidation entirely.

    That must be visible - it silently turns a revocation into a
    stale-cache window - but it is a static property of the configured
    backend, so it is reported once per process rather than on every save.
    Before that, a single test run emitted the same warning 1330 times.
    """

    # patch.object rather than assigning the module attribute directly: it
    # restores the flag afterwards, so consuming the single warning here
    # cannot silence an unrelated test that runs later in the same process.
    def _unreported(self):
        return patch.object(signals, "_warned_backend_lacks_delete_pattern", False)

    @override_settings(
        CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    )
    def test_it_warns_once_and_then_stays_quiet(self):
        with self._unreported():
            with self.assertLogs("classrooms.signals", level="WARNING") as captured:
                signals.delete_cache_patterns("a:*", "b:*")
                signals.delete_cache_patterns("c:*")
                signals.delete_cache_patterns("d:*")

        warnings = [line for line in captured.output if "no delete_pattern" in line]
        self.assertEqual(
            len(warnings),
            1,
            f"expected exactly one warning per process, got {len(warnings)}",
        )

    @override_settings(
        CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    )
    def test_the_warning_names_the_backend_and_the_consequence(self):
        with self._unreported():
            with self.assertLogs("classrooms.signals", level="WARNING") as captured:
                signals.delete_cache_patterns("a:*")

        message = captured.output[0]
        self.assertIn("stale", message.lower())
        self.assertIn("revoked", message.lower())
