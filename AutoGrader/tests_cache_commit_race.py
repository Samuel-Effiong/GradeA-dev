"""A generation bump inside a transaction must not let a concurrent read
cache pre-commit data under the new generation.

`bump_many` runs from `post_save`/`post_delete`. When the save is inside
`transaction.atomic`, the bump lands before the commit. A reader in that
window reads the bumped generation but the old committed rows, and caches
old data under a key that nothing invalidates until its TTL.

The race is forced, not left to timing: the writer's bump releases a reader
thread and holds the writer's transaction open until the reader has cached
its response, and only then does the writer commit.

Real Redis + real Postgres, a separate DB connection per thread.
"""

import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import redis
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connections, transaction
from django.test import TransactionTestCase
from django.urls import reverse
from django_redis.client import DefaultClient
from rest_framework.test import APIClient

import AutoGrader.cache_generation as cache_generation
import classrooms.signals
from AutoGrader.cache_generation import (
    SCOPE_COURSE,
    SCOPE_SCHOOL,
    SCOPE_USER,
    bump_many,
    get_generation,
    versioned_key,
)
from classrooms.models import (
    Course,
    EnrollmentStatusType,
    School,
    Session,
    StudentCourse,
)
from classrooms.services.enrollment import enroll_student_by_email
from users.models import UserTypes

User = get_user_model()

LEGACY_MODULES = (
    "AutoGrader.cache_utils",
    "classrooms.signals",
    "users.signals",
    "students.signals",
    "assignments.signals",
)


def make_user(email, user_type, first_name):
    return User.objects.create_user(
        email=email,
        password="password123",  # nosec  # pragma: allowlist secret
        user_type=user_type,
        is_active=True,
        first_name=first_name,
        last_name=user_type.title(),
    )


def run_threads(targets, timeout=60):
    """Start every target, join them, and fail loudly on a hung thread."""
    threads = [threading.Thread(target=target) for target in targets]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=timeout)
    stuck = [thread for thread in threads if thread.is_alive()]
    assert not stuck, f"{len(stuck)} thread(s) never finished"


class CommitRaceBase(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        cache.clear()
        # The legacy wildcard deletes also run before commit; disabling them
        # isolates the generation mechanism this change is about.
        for module in LEGACY_MODULES:
            imported = __import__(module, fromlist=["delete_cache_patterns"])
            if hasattr(imported, "delete_cache_patterns"):
                self.enterContext(
                    patch.object(
                        imported, "delete_cache_patterns", lambda *a, **k: None
                    )
                )
        self.enterContext(
            patch("classrooms.services.notifications.safe_delay", lambda *a, **k: None)
        )
        self.teacher = make_user("race-t@x.test", UserTypes.TEACHER, "RaceT")
        term = Session.objects.create(name="Race term", teacher=self.teacher)
        self.course = Course.objects.create(
            name="Race course", teacher=self.teacher, session=term
        )
        self.detail = reverse("course-detail", args=[self.course.pk])

    def read(self):
        client = APIClient()
        client.force_authenticate(self.teacher)
        response = client.get(self.detail)
        self.assertEqual(response.status_code, 200, response.content)
        return response.data

    def truth(self):
        with patch.object(cache, "get", lambda *a, **k: None), patch.object(
            cache, "set", lambda *a, **k: True
        ):
            return self.read()

    def assert_cached_read_is_fresh(self, why=None):
        self.assertEqual(
            self.read(),
            self.truth(),
            why
            or "the cached course is stale: a read in the pre-commit window was "
            "cached under the new generation",
        )

    def race(self, write, errors):
        """Run `write` in a thread; its first classrooms bump releases a
        reader, and holds the write's transaction open until the reader has
        cached what it saw."""
        bumped = threading.Event()
        reader_cached = threading.Event()
        writer = {}

        def gated(scopes):
            result = bump_many(scopes)
            if threading.get_ident() == writer.get("id") and not bumped.is_set():
                bumped.set()
                if not reader_cached.wait(timeout=30):
                    errors.append("reader never cached")
            return result

        def writer_body():
            writer["id"] = threading.get_ident()
            try:
                write()
            except BaseException as exc:  # noqa: BLE001 - asserted by caller
                errors.append(repr(exc))
            finally:
                bumped.set()
                connections.close_all()

        def reader_body():
            try:
                if not bumped.wait(timeout=30):
                    errors.append("writer never bumped")
                    return
                self.read()
            except BaseException as exc:  # noqa: BLE001 - asserted by caller
                errors.append(repr(exc))
            finally:
                reader_cached.set()
                connections.close_all()

        with patch.object(classrooms.signals, "bump_many", gated):
            run_threads([writer_body, reader_body])


class ForcedInterleavingTests(CommitRaceBase):
    def setUp(self):
        super().setUp()
        self.student = make_user("race-s@x.test", UserTypes.STUDENT, "RaceS")

    def test_a_read_in_the_pre_commit_window_is_not_served_stale(self):
        self.read()  # warm
        errors = []
        self.race(
            lambda: enroll_student_by_email(
                course=self.course, email=self.student.email
            ),
            errors,
        )
        self.assertEqual(errors, [])
        self.assertTrue(
            StudentCourse.objects.filter(
                student=self.student, course=self.course
            ).exists()
        )
        self.assert_cached_read_is_fresh()

    def test_a_nested_caller_with_a_rolled_back_savepoint_is_not_served_stale(self):
        """Enrollment (itself atomic) inside an outer transaction that also
        rolls back an unrelated inner savepoint, then commits."""
        self.read()  # warm
        errors = []

        def write():
            with transaction.atomic():
                enroll_student_by_email(course=self.course, email=self.student.email)
                try:
                    with transaction.atomic():
                        # Must not bump anything the teacher's course-detail
                        # key depends on: a bump after the reader cached
                        # would hide the race this test exists to catch.
                        School.objects.create(name="discarded")
                        raise RuntimeError("inner savepoint rolls back")
                except RuntimeError:
                    pass

        self.race(write, errors)
        self.assertEqual(errors, [])
        self.assertFalse(School.objects.filter(name="discarded").exists())
        self.assert_cached_read_is_fresh()


class TransactionBumpCountTests(CommitRaceBase):
    """Exact generation arithmetic, so the post-commit bump can neither be
    lost nor fire twice."""

    def gen(self):
        return get_generation(SCOPE_USER, self.teacher.pk)

    def test_outside_a_transaction_a_bump_happens_once(self):
        start = self.gen()
        bump_many([(SCOPE_USER, self.teacher.pk)])
        self.assertEqual(self.gen(), start + 1)

    def test_a_committed_transaction_bumps_before_and_after_commit(self):
        start = self.gen()
        with transaction.atomic():
            bump_many([(SCOPE_USER, self.teacher.pk)])
            self.assertEqual(self.gen(), start + 1, "the immediate bump is gone")
        self.assertEqual(self.gen(), start + 2, "post-commit bump lost or doubled")

    def test_a_rolled_back_transaction_does_not_bump_after_commit(self):
        start = self.gen()
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                bump_many([(SCOPE_USER, self.teacher.pk)])
                raise RuntimeError("roll back")
        self.assertEqual(self.gen(), start + 1)

    def test_an_inner_savepoint_rollback_keeps_the_outer_post_commit_bump(self):
        start = self.gen()
        with transaction.atomic():
            bump_many([(SCOPE_USER, self.teacher.pk)])
            try:
                with transaction.atomic():
                    raise RuntimeError("inner rolls back")
            except RuntimeError:
                pass
        self.assertEqual(self.gen(), start + 2)

    def test_a_bump_inside_a_rolled_back_savepoint_is_not_repeated_on_commit(self):
        start = self.gen()
        with transaction.atomic():
            try:
                with transaction.atomic():
                    bump_many([(SCOPE_USER, self.teacher.pk)])
                    raise RuntimeError("inner rolls back")
            except RuntimeError:
                pass
        self.assertEqual(self.gen(), start + 1)

    def test_nested_committed_savepoints_bump_once_after_the_outer_commit(self):
        start = self.gen()
        with transaction.atomic():
            with transaction.atomic():
                bump_many([(SCOPE_USER, self.teacher.pk)])
            self.assertEqual(self.gen(), start + 1)
        self.assertEqual(self.gen(), start + 2)


class RedisDownAtCommitTests(CommitRaceBase):
    """If Redis fails exactly when the post-commit bump runs, the write still
    commits and the poisoned entry is bounded by its TTL."""

    def setUp(self):
        super().setUp()
        self.student = make_user("race-rd@x.test", UserTypes.STUDENT, "RaceRD")

    def test_the_write_commits_and_staleness_ends_when_the_entry_expires(self):
        self.read()  # warm
        errors = []
        redis_down = {"on": False}
        # Patched on the client CLASS: Django's cache object is per-thread,
        # so patching the test thread's instance never reaches the writer.
        real_get_client = DefaultClient.get_client

        def broken_get_client(self, *args, **kwargs):
            if redis_down["on"]:
                raise ConnectionError("redis unavailable at commit")
            return real_get_client(self, *args, **kwargs)

        def write():
            with transaction.atomic():
                enroll_student_by_email(course=self.course, email=self.student.email)
                # The reader has cached by now (the race gate waits for it);
                # Redis goes away before the transaction commits.
                redis_down["on"] = True

        with patch.object(DefaultClient, "get_client", broken_get_client):
            self.race(write, errors)
        redis_down["on"] = False

        self.assertEqual(errors, [], "the write failed because Redis was down")
        self.assertTrue(
            StudentCourse.objects.filter(
                student=self.student, course=self.course
            ).exists()
        )

        key = versioned_key(
            f"courses:user_id__{self.teacher.pk}:instance_id__{self.course.pk}",
            [(SCOPE_USER, self.teacher.pk)],
        )
        ttl = cache.ttl(key)  # type: ignore[attr-defined]  # django-redis
        self.assertIsNotNone(ttl, "the poisoned entry has no expiry")
        self.assertGreater(ttl, 0)
        self.assertLessEqual(ttl, 300, "stale for longer than the 5-minute TTL")

        # The bound itself: once the entry expires, the next read is fresh.
        cache.expire(key, 1)  # type: ignore[attr-defined]  # django-redis
        time.sleep(1.5)
        self.assert_cached_read_is_fresh()


class WriterRedisFailureTests(CommitRaceBase):
    """Redis fails for the WRITER only, at each step of its bumps.

    Only the writer's thread sees the failure, so the reader still caches
    what it reads in the window, which is the case that matters. Both a
    refused connection and a timeout are injected, at the class level for
    the reason given in RedisDownAtCommitTests.
    """

    FAILURES = (
        ConnectionError("redis unavailable"),
        redis.exceptions.TimeoutError("redis timed out"),
    )

    def setUp(self):
        super().setUp()
        self.student = make_user("race-wf@x.test", UserTypes.STUDENT, "RaceWF")

    def race_with_writer_failure(self, failure, *, recovers_before_commit):
        failing = threading.Event()
        writer_thread = []
        injected = []
        real_get_client = DefaultClient.get_client

        def get_client(client, *args, **kwargs):
            if failing.is_set() and threading.get_ident() in writer_thread:
                injected.append(type(failure).__name__)
                raise failure
            return real_get_client(client, *args, **kwargs)

        def write():
            writer_thread.append(threading.get_ident())
            failing.set()
            with transaction.atomic():
                enroll_student_by_email(course=self.course, email=self.student.email)
                if recovers_before_commit:
                    failing.clear()

        errors = []
        with patch.object(DefaultClient, "get_client", get_client):
            self.race(write, errors)
        failing.clear()

        self.assertEqual(errors, [], "a Redis failure failed the write")
        self.assertTrue(injected, "no failure was injected")
        self.assertTrue(
            StudentCourse.objects.filter(
                student=self.student, course=self.course
            ).exists()
        )

    def test_a_failed_in_transaction_bump_is_recovered_by_the_post_commit_bump(self):
        for failure in self.FAILURES:
            with self.subTest(failure=type(failure).__name__):
                StudentCourse.objects.filter(student=self.student).delete()
                self.read()  # warm
                self.race_with_writer_failure(failure, recovers_before_commit=True)
                self.assert_cached_read_is_fresh()

    def test_both_bumps_failing_leaves_staleness_bounded_by_the_ttl(self):
        for failure in self.FAILURES:
            with self.subTest(failure=type(failure).__name__):
                StudentCourse.objects.filter(student=self.student).delete()
                cache.clear()
                self.read()  # warm
                self.race_with_writer_failure(failure, recovers_before_commit=False)

                key = versioned_key(
                    f"courses:user_id__{self.teacher.pk}"
                    f":instance_id__{self.course.pk}",
                    [(SCOPE_USER, self.teacher.pk)],
                )
                ttl = cache.ttl(key)  # type: ignore[attr-defined]  # django-redis
                self.assertIsNotNone(ttl, "the stale entry has no expiry")
                self.assertGreater(ttl, 0)
                self.assertLessEqual(ttl, 300)
                cache.expire(key, 1)  # type: ignore[attr-defined]  # django-redis
                time.sleep(1.5)
                self.assert_cached_read_is_fresh()


class PostCommitBumpIsolationTests(CommitRaceBase):
    """The post-commit bump repeats the writer's own scopes and nothing else:
    no other teacher, course or school, in the same school or another."""

    def setUp(self):
        super().setUp()
        self.school = School.objects.create(name="Race home school")
        self.other_school = School.objects.create(name="Race other school")
        self.teacher.school = self.school
        self.teacher.save()
        self.student = make_user("race-iso@x.test", UserTypes.STUDENT, "RaceIso")

        self.same_school_teacher = make_user(
            "race-t2@x.test", UserTypes.TEACHER, "RaceT2"
        )
        self.same_school_teacher.school = self.school
        self.same_school_teacher.save()
        self.other_school_teacher = make_user(
            "race-t3@x.test", UserTypes.TEACHER, "RaceT3"
        )
        self.other_school_teacher.school = self.other_school
        self.other_school_teacher.save()
        self.other_student = make_user(
            "race-iso2@x.test", UserTypes.STUDENT, "RaceIso2"
        )

        self.same_school_course = Course.objects.create(
            name="Neighbour course",
            teacher=self.same_school_teacher,
            session=Session.objects.create(
                name="Neighbour term", teacher=self.same_school_teacher
            ),
        )
        self.other_school_course = Course.objects.create(
            name="Foreign course",
            teacher=self.other_school_teacher,
            session=Session.objects.create(
                name="Foreign term", teacher=self.other_school_teacher
            ),
        )

    def generations(self, pairs):
        return {pair: get_generation(*pair) for pair in pairs}

    def test_only_the_writers_scopes_move_and_the_commit_exactly_repeats_them(self):
        own = [
            (SCOPE_USER, self.student.pk),
            (SCOPE_USER, self.teacher.pk),
            (SCOPE_COURSE, self.course.pk),
            (SCOPE_SCHOOL, self.school.pk),
        ]
        foreign = [
            (SCOPE_USER, self.same_school_teacher.pk),
            (SCOPE_USER, self.other_school_teacher.pk),
            (SCOPE_USER, self.other_student.pk),
            (SCOPE_COURSE, self.same_school_course.pk),
            (SCOPE_COURSE, self.other_school_course.pk),
            (SCOPE_SCHOOL, self.other_school.pk),
        ]
        own_before = self.generations(own)
        foreign_before = self.generations(foreign)

        with transaction.atomic():
            enroll_student_by_email(course=self.course, email=self.student.email)
            in_transaction = {
                pair: gen - own_before[pair]
                for pair, gen in self.generations(own).items()
            }
            self.assertEqual(self.generations(foreign), foreign_before)

        self.assertTrue(
            all(delta >= 1 for delta in in_transaction.values()), in_transaction
        )
        after_commit = {
            pair: gen - own_before[pair] for pair, gen in self.generations(own).items()
        }
        self.assertEqual(
            after_commit,
            {pair: 2 * delta for pair, delta in in_transaction.items()},
        )
        self.assertEqual(self.generations(foreign), foreign_before)


class CrashAfterCommitTests(CommitRaceBase):
    """The in-transaction bump is what still protects readers when the
    post-commit bump never runs: the process dies between COMMIT and the
    on_commit callbacks, or they are otherwise lost."""

    def setUp(self):
        super().setUp()
        self.student = make_user("race-crash@x.test", UserTypes.STUDENT, "RaceCr")

    def test_pre_write_entries_are_orphaned_even_if_on_commit_never_runs(self):
        self.read()  # warm, before the write
        callbacks_lost = SimpleNamespace(
            get_connection=transaction.get_connection,
            on_commit=lambda *a, **k: None,
        )
        with patch.object(cache_generation, "transaction", callbacks_lost):
            enroll_student_by_email(course=self.course, email=self.student.email)

        self.assertTrue(
            StudentCourse.objects.filter(
                student=self.student, course=self.course
            ).exists()
        )
        # No reader raced the write, so only the in-transaction bump stands
        # between this read and the entry cached before the enrollment.
        self.assert_cached_read_is_fresh(
            "the entry cached before the write is still served: with the "
            "post-commit bump lost, nothing moved the generation"
        )


class ConcurrentEnrollmentRaceLoadTests(CommitRaceBase):
    ROUNDS = 10
    WRITERS = 20

    def _writer(self, barrier, errors, student):
        def body():
            try:
                barrier.wait(timeout=30)
                enroll_student_by_email(course=self.course, email=student.email)
            except BaseException as exc:  # noqa: BLE001 - asserted per round
                errors.append(repr(exc))
            finally:
                connections.close_all()

        return body

    def _reader(self, barrier, errors):
        def body():
            try:
                barrier.wait(timeout=30)
                self.read()
            except BaseException as exc:  # noqa: BLE001 - asserted per round
                errors.append(repr(exc))
            finally:
                connections.close_all()

        return body

    def test_twenty_concurrent_enrollments_racing_reads_settle_fresh_every_round(self):
        for round_number in range(self.ROUNDS):
            students = [
                make_user(
                    f"race-load-{round_number}-{i}@x.test",
                    UserTypes.STUDENT,
                    f"Load{round_number}x{i}",
                )
                for i in range(self.WRITERS)
            ]
            self.read()  # warm before the burst
            barrier = threading.Barrier(self.WRITERS * 2)
            errors = []
            run_threads(
                [self._writer(barrier, errors, s) for s in students]
                + [self._reader(barrier, errors)] * self.WRITERS,
                timeout=120,
            )
            self.assertEqual(errors, [], f"round {round_number}")
            self.assertEqual(
                StudentCourse.objects.filter(
                    course=self.course,
                    enrollment_status=EnrollmentStatusType.ENROLLED,
                ).count(),
                self.WRITERS * (round_number + 1),
            )
            self.assert_cached_read_is_fresh()
