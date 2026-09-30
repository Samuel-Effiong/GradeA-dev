"""An enrolment refreshes its classmates' cached roster at a fixed cost.

CourseSerializer shows a student their classmates (`students`) and the
course's `student_count`, so another student's enrolment changes what a
classmate's cached course list and detail should say. Stage 3 (G5) made
that fresh by bumping every enrolled student's own generation on every
enrolment write: O(class size) bumps per enrolment, O(n^2) for a roster
import, which hung the H-25 cost harness at 6,000 enrolments.

Now an enrolment write bumps a fixed five scopes (the student, `global`,
the course's `crs`, its teacher and the teacher's school), and a student's
cached course list and detail are keyed on the `crs` generation of every
course in them (CourseViewSet.extra_cache_scopes). `my-courses` is keyed on
`global`, which the enrolment also bumps.

This suite proves both halves: the classmate's cached reads are FRESH after
an enrolment and after a removal, through the real endpoints, and the cost
of one enrolment is the same at a class of 30 and a class of 300.

Real Redis + real Postgres.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.core.cache import cache
from django.db import connection
from django.test import TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework.test import APIClient

import classrooms.signals
from AutoGrader.cache_generation import (
    DEFAULT_GENERATION,
    SCOPE_COURSE,
    SCOPE_GLOBAL,
    SCOPE_SCHOOL,
    SCOPE_USER,
    bump_many,
    generation_key,
    get_generations,
    versioned_key,
)
from AutoGrader.tests_cache_generation import redis_commands_sent_by_this_process
from AutoGrader.tests_cache_matrix_support import UNAFFECTED, FreshnessMatrixMixin, Read
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


def make_active_user(email, user_type, first_name, **extra):
    return User.objects.create_user(
        email=email,
        password="password123",  # nosec  # pragma: allowlist secret
        user_type=user_type,
        is_active=True,
        first_name=first_name,
        last_name=user_type.title(),
        **extra,
    )


class RosterBase(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        cache.clear()
        # Step 4 removed the legacy wildcard deletes, so these tests run on
        # the real code with nothing patched out (on stage 3 they switched
        # the wildcards off); run_matrix fails any SCAN a mutation sends.
        # Enrolment and removal email the student through Celery.
        self.enterContext(
            patch("classrooms.services.notifications.safe_delay", lambda *a, **k: None)
        )
        self.school = School.objects.create(name="Roster school")
        self.teacher = make_active_user(
            "roster-t@x.test", UserTypes.TEACHER, "RosterT", school=self.school
        )
        self.course = Course.objects.create(
            name="Roster course",
            teacher=self.teacher,
            session=Session.objects.create(name="Roster term", teacher=self.teacher),
        )


class ClassmateRosterFreshnessTests(FreshnessMatrixMixin, RosterBase):
    """A classmate's cached course list, detail and my-courses must show a
    newcomer, and stop showing a student who was removed."""

    def setUp(self):
        super().setUp()
        self.classmate = make_active_user(
            "roster-classmate@x.test", UserTypes.STUDENT, "RosterClassmate"
        )
        self.newcomer = make_active_user(
            "roster-newcomer@x.test", UserTypes.STUDENT, "RosterNewcomer"
        )
        self.outsider = make_active_user(
            "roster-outsider@x.test", UserTypes.STUDENT, "RosterOutsider"
        )
        StudentCourse.objects.create(
            student=self.classmate,
            course=self.course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )
        self.detail_url = reverse("course-detail", args=[self.course.pk])
        self.list_url = reverse("course-list")
        self.my_courses_url = reverse("course-my-courses")

    def reads(self):
        return [
            Read("classmate's course list", self.classmate, self.list_url),
            Read("classmate's course detail", self.classmate, self.detail_url),
            Read("classmate's my-courses", self.classmate, self.my_courses_url),
            Read("teacher's course detail", self.teacher, self.detail_url),
            Read("outside student's course detail", self.outsider, self.detail_url),
        ]

    def teacher_client(self):
        client = APIClient()
        client.force_authenticate(self.teacher)
        return client

    def enrol_newcomer(self):
        response = self.teacher_client().post(
            reverse("course-students", args=[self.course.pk]),
            {"email": self.newcomer.email},
            format="json",
        )
        self.assertLess(response.status_code, 300, response.content)

    def remove_classmate_peer(self):
        response = self.teacher_client().delete(
            reverse("course-remove-student", args=[self.course.pk, self.newcomer.pk])
        )
        self.assertLess(response.status_code, 300, response.content)

    def assert_classmate_reads_fresh(self, result):
        self.assert_no_stale(
            result,
            expect_changed=[
                "classmate's course list",
                "classmate's course detail",
                "classmate's my-courses",
                "teacher's course detail",
            ],
        )
        verdicts = {o.label: o.verdict for o in result.outcomes}
        # No access before or after (404 both times), so it never moves.
        self.assertEqual(
            verdicts["outside student's course detail"], UNAFFECTED, result.table()
        )

    def test_an_enrolment_refreshes_the_classmates_cached_roster(self):
        result = self.run_matrix("enrol a newcomer", self.reads(), self.enrol_newcomer)
        self.assert_classmate_reads_fresh(result)

    def test_a_removal_refreshes_the_classmates_cached_roster(self):
        StudentCourse.objects.create(
            student=self.newcomer,
            course=self.course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )
        result = self.run_matrix(
            "remove a classmate", self.reads(), self.remove_classmate_peer
        )
        self.assert_classmate_reads_fresh(result)


class EnrolmentBumpCostTests(RosterBase):
    """One enrolment costs the same whatever the size of the class."""

    def fill_class(self, size, tag):
        password = make_password("password123")  # nosec  # pragma: allowlist secret
        User.objects.bulk_create(
            User(
                email=f"roster-{tag}-{i}@x.test",
                password=password,
                user_type=UserTypes.STUDENT,
                is_active=True,
                first_name=f"Roster{tag}x{i}",
                last_name="Student",
            )
            for i in range(size)
        )
        # bulk_create sends no signals: the class is filled without paying
        # the very cost this test measures.
        StudentCourse.objects.bulk_create(
            StudentCourse(
                student=student,
                course=self.course,
                enrollment_status=EnrollmentStatusType.ENROLLED,
            )
            for student in User.objects.filter(email__startswith=f"roster-{tag}-")
        )

    def measure_one_enrolment(self, tag):
        newcomer = make_active_user(
            f"roster-new-{tag}@x.test", UserTypes.STUDENT, f"RosterNew{tag}"
        )
        bumped = []

        def recording_bump_many(scopes):
            scopes = list(scopes)
            bumped.extend(scopes)
            return bump_many(scopes)

        with patch.object(
            classrooms.signals, "bump_many", recording_bump_many
        ), redis_commands_sent_by_this_process() as sent:
            enroll_student_by_email(course=self.course, email=newcomer.email)
        return newcomer, bumped, dict(sent)

    def test_one_enrolment_bumps_five_scopes_at_a_class_of_30_and_of_300(self):
        results = {}
        for size in (30, 300):
            self.course = Course.objects.create(
                name=f"Roster course {size}",
                teacher=self.teacher,
                session=self.course.session,
            )
            self.fill_class(size, f"c{size}")
            newcomer, bumped, commands = self.measure_one_enrolment(f"c{size}")
            self.assertEqual(
                sorted(map(str, bumped)),
                sorted(
                    map(
                        str,
                        [
                            (SCOPE_USER, newcomer.pk),
                            (SCOPE_GLOBAL, None),
                            (SCOPE_COURSE, self.course.pk),
                            (SCOPE_USER, self.teacher.pk),
                            (SCOPE_SCHOOL, self.school.pk),
                        ],
                    )
                ),
                f"class of {size}: an enrolment must bump exactly these five "
                "scopes and no classmate",
            )
            results[size] = commands
            print(
                f"\n[roster-scope write cost] one enrolment, class of {size}: "
                f"{sum(commands.values())} Redis commands {commands}",
                flush=True,
            )
        self.assertEqual(
            results[30],
            results[300],
            f"Redis commands for one enrolment must not grow with the class: {results}",
        )
        # Pinned exactly: one pipeline of SET NX + INCR per scope, sent
        # twice, because the enrolment commits inside an atomic block and
        # H-25 replays every in-transaction bump once at commit (stage 3
        # alone, without H-25, pins {"SET": 5, "INCR": 5}). The old
        # per-classmate fan-out sent 2 per enrolled student on top.
        self.assertEqual(results[30], {"SET": 10, "INCR": 10})


class StudentCourseKeyShapeTests(RosterBase):
    """What the keys actually carry, per viewer."""

    def setUp(self):
        super().setUp()
        self.student = make_active_user(
            "roster-keys@x.test", UserTypes.STUDENT, "RosterKeys"
        )
        self.second = Course.objects.create(
            name="Roster course 2", teacher=self.teacher, session=self.course.session
        )
        for course in (self.course, self.second):
            StudentCourse.objects.create(
                student=self.student,
                course=course,
                enrollment_status=EnrollmentStatusType.ENROLLED,
            )

    def cache_keys_set_by(self, user, url):
        keys = []
        real_set = cache.set

        def recording_set(key, *args, **kwargs):
            keys.append(key)
            return real_set(key, *args, **kwargs)

        client = APIClient()
        client.force_authenticate(user)
        with patch.object(cache, "set", recording_set):
            response = client.get(url)
        self.assertEqual(response.status_code, 200, response.content)
        return [k for k in keys if k.startswith("courses:")]

    def test_a_students_detail_key_carries_that_course(self):
        [key] = self.cache_keys_set_by(
            self.student, reverse("course-detail", args=[self.course.pk])
        )
        self.assertEqual(key.count(f"{SCOPE_COURSE}="), 1, key)

    def test_a_students_list_key_carries_every_enrolled_course(self):
        [key] = self.cache_keys_set_by(self.student, reverse("course-list"))
        self.assertEqual(key.count(f"{SCOPE_COURSE}="), 2, key)

    def test_a_silent_withdrawal_moves_the_students_list_key(self):
        """The list key names the courses the student can see, read live.
        So a withdrawal that bumps nothing (QuerySet.update) still moves the
        key: the student never sees a course they have left, even from the
        cache. The matrix self-test uses a rename instead for that reason."""
        [before] = self.cache_keys_set_by(self.student, reverse("course-list"))
        StudentCourse.objects.filter(student=self.student, course=self.second).update(
            enrollment_status=EnrollmentStatusType.WITHDRAWN
        )
        [after] = self.cache_keys_set_by(self.student, reverse("course-list"))
        self.assertNotEqual(after, before)
        self.assertEqual(after.count(f"{SCOPE_COURSE}="), 1, after)

    def test_a_teachers_keys_do_not(self):
        for url in (
            reverse("course-list"),
            reverse("course-detail", args=[self.course.pk]),
        ):
            with self.subTest(url=url):
                [key] = self.cache_keys_set_by(self.teacher, url)
                self.assertNotIn(f"{SCOPE_COURSE}=", key)

    def hit_cost(self, user, url):
        """(DB queries, Redis commands, Redis round trips) of a cache HIT."""
        client = APIClient()
        client.force_authenticate(user)
        self.assertEqual(client.get(url).status_code, 200)  # warm
        with CaptureQueriesContext(connection) as queries:
            with redis_commands_sent_by_this_process() as sent:
                response = client.get(url)
        self.assertEqual(response.status_code, 200)
        return len(queries), dict(sent)

    def test_what_a_cache_hit_costs_a_student_compared_with_a_teacher(self):
        """The price of keying a student's course payload on its courses:
        on a list hit, one extra query (the student's course ids) and no
        extra Redis round trip, because every generation is read in one
        MGET. A detail hit costs nothing extra: the course comes from the
        URL. Measured against a teacher's hit, whose key is unchanged, so
        anything the middleware does per request cancels out."""
        costs = {}
        for label, url in (
            ("list", reverse("course-list")),
            ("detail", reverse("course-detail", args=[self.course.pk])),
        ):
            teacher = self.hit_cost(self.teacher, url)
            student = self.hit_cost(self.student, url)
            costs[label] = {"teacher": teacher, "student": student}
            print(
                f"\n[roster-scope read cost] {label} hit: teacher "
                f"queries={teacher[0]} redis={teacher[1]} | student "
                f"queries={student[0]} redis={student[1]}",
                flush=True,
            )
            self.assertEqual(
                sum(student[1].values()),
                sum(teacher[1].values()),
                f"{label}: a student's hit must cost no extra Redis round trip",
            )
            self.assertEqual(student[1].get("MGET", 0), 1, label)
        self.assertEqual(costs["list"]["student"][0], costs["list"]["teacher"][0] + 1)
        self.assertEqual(costs["detail"]["student"][0], costs["detail"]["teacher"][0])


class BatchedGenerationReadTests(TransactionTestCase):
    """`versioned_key(batched=True)`: one round trip, same key, same
    never-raise degradation as `get_generation`."""

    def setUp(self):
        cache.clear()

    def test_batched_and_unbatched_build_the_same_key(self):
        scopes = [(SCOPE_USER, "u1"), (SCOPE_COURSE, "c1"), (SCOPE_COURSE, "c2")]
        bump_many([(SCOPE_COURSE, "c1")])
        self.assertEqual(
            versioned_key("base", scopes, batched=True),
            versioned_key("base", scopes),
        )

    def test_batched_reads_every_generation_in_one_command(self):
        scopes = [(SCOPE_USER, "u1")] + [(SCOPE_COURSE, f"c{i}") for i in range(8)]
        with redis_commands_sent_by_this_process() as sent:
            versioned_key("base", scopes, batched=True)
        self.assertEqual(dict(sent), {"MGET": 1})

    def test_missing_and_corrupt_counters_read_as_the_default(self):
        cache.set(generation_key(SCOPE_COURSE, "bad"), "not-a-number")
        self.assertEqual(
            get_generations([(SCOPE_COURSE, "missing"), (SCOPE_COURSE, "bad")]),
            [DEFAULT_GENERATION, DEFAULT_GENERATION],
        )

    def test_an_unreachable_redis_reads_as_the_default_and_never_raises(self):
        with patch.object(cache, "get_many", side_effect=ConnectionError("down")):
            self.assertEqual(
                get_generations([(SCOPE_USER, "u1"), (SCOPE_COURSE, "c1")]),
                [DEFAULT_GENERATION, DEFAULT_GENERATION],
            )
