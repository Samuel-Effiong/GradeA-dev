"""H-1 Stage 3 item 7: a change to a user's OWN row must reach every other
user's cached view that displays or counts that user.

A legacy-disabled probe on `1373eae` found 16 stale (mutation, endpoint)
pairs (docs/evidence/H1_H2_RELEASE_GATE_EVIDENCE.md §3). A CustomUser save
bumped only that user's generation, while the views showing the user to
SOMEONE ELSE are keyed on the school or on the viewing teacher.

The fix (owner-approved, precise fan-out, no global flush):
* the user's current school - and the previous school on a move;
* for students, the teachers of their courses and those teachers' schools;
* only when a field another user can see actually changed.

Every freshness test here runs with the legacy wildcard receivers DISABLED.
While they run, `*user*`/`*school*`/`*course*` sweeps hide exactly this
class of bug, so a test under dual-running would prove nothing.

Real Redis + real Postgres.
"""

import threading
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from redis.exceptions import ConnectionError as RedisConnectionError
from rest_framework.test import APIClient

from assignments.models import Assignment, AssignmentStatus
from AutoGrader.cache_generation import (
    SCOPE_ANY_SCHOOL,
    SCOPE_COURSE,
    SCOPE_SCHOOL,
    SCOPE_USER,
    get_generation,
)
from classrooms.models import (
    Course,
    EnrollmentStatusType,
    School,
    Session,
    StudentCourse,
)
from students.models import StudentSubmission
from users.models import Settings, UserTypes

User = get_user_model()

LEGACY_MODULES = (
    "classrooms.signals",
    "users.signals",
    "students.signals",
    "assignments.signals",
)


def make_user(email, user_type, school=None, first="First", last="Last"):
    user = User.objects.create_user(
        email=email,
        password="password123",  # nosec  # pragma: allowlist secret
    )
    user.user_type = user_type
    user.is_active = True
    user.school = school
    user.first_name = first
    user.last_name = last
    user.save()
    return user


class UserFanoutBase(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        cache.clear()
        for module in LEGACY_MODULES:
            p = patch(f"{module}.delete_cache_patterns", lambda *a, **k: None)
            p.start()
            self.addCleanup(p.stop)

        self.school_a = School.objects.create(name="Fanout A")
        self.school_b = School.objects.create(name="Fanout B")
        self.school_c = School.objects.create(name="Fanout C")

        self.admin_a = make_user("fo-aa@x.test", UserTypes.SCHOOL_ADMIN, self.school_a)
        self.teacher_a = make_user("fo-ta@x.test", UserTypes.TEACHER, self.school_a)
        # Same school, no relationship to the student below.
        self.teacher_a2 = make_user("fo-ta2@x.test", UserTypes.TEACHER, self.school_a)
        self.admin_b = make_user("fo-ab@x.test", UserTypes.SCHOOL_ADMIN, self.school_b)
        self.teacher_b = make_user("fo-tb@x.test", UserTypes.TEACHER, self.school_b)

        self.student = make_user(
            "fo-s@x.test", UserTypes.STUDENT, None, "Sam", "Student"
        )
        self.bystander = make_user(
            "fo-u@x.test", UserTypes.STUDENT, None, "Una", "Related"
        )

        self.session_a = Session.objects.create(name="FA", teacher=self.teacher_a)
        self.course_a = Course.objects.create(
            name="FA 101", teacher=self.teacher_a, session=self.session_a
        )
        self.session_a2 = Session.objects.create(name="FA2", teacher=self.teacher_a2)
        self.course_a2 = Course.objects.create(
            name="FA2 101", teacher=self.teacher_a2, session=self.session_a2
        )
        self.session_b = Session.objects.create(name="FB", teacher=self.teacher_b)
        self.course_b = Course.objects.create(
            name="FB 101", teacher=self.teacher_b, session=self.session_b
        )
        StudentCourse.objects.create(
            student=self.student,
            course=self.course_a,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )
        StudentCourse.objects.create(
            student=self.bystander,
            course=self.course_a2,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )
        StudentCourse.objects.create(
            student=self.bystander,
            course=self.course_b,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )
        self.assignment_a = Assignment.objects.create(
            title="FA work",
            course=self.course_a,
            teacher=self.teacher_a,
            status=AssignmentStatus.PUBLISHED,
        )
        StudentSubmission.objects.create(
            student=self.student,
            assignment=self.assignment_a,
            answers={},
            score=80,
            max_points=100,
            graded_at=timezone.now(),
            grading_confidence=0.9,
        )

    def tearDown(self):
        cache.clear()

    def client_for(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def generations(self):
        return {
            "sch_a": get_generation(SCOPE_SCHOOL, self.school_a.id),
            "sch_b": get_generation(SCOPE_SCHOOL, self.school_b.id),
            "sch_c": get_generation(SCOPE_SCHOOL, self.school_c.id),
            "anysch": get_generation(SCOPE_ANY_SCHOOL),
            "usr_teacher_a": get_generation(SCOPE_USER, self.teacher_a.id),
            "usr_teacher_a2": get_generation(SCOPE_USER, self.teacher_a2.id),
            "usr_teacher_b": get_generation(SCOPE_USER, self.teacher_b.id),
            "usr_admin_a": get_generation(SCOPE_USER, self.admin_a.id),
            "usr_admin_b": get_generation(SCOPE_USER, self.admin_b.id),
            "usr_student": get_generation(SCOPE_USER, self.student.id),
            "usr_bystander": get_generation(SCOPE_USER, self.bystander.id),
            "crs_a": get_generation(SCOPE_COURSE, self.course_a.id),
        }

    def assert_moved(self, before, *names):
        after = self.generations()
        for name in names:
            self.assertGreater(after[name], before[name], f"{name} did not move")

    def assert_still(self, before, *names):
        after = self.generations()
        for name in names:
            self.assertEqual(
                after[name], before[name], f"{name} moved - over-invalidation"
            )


class LegacyReallyDisabledTests(UserFanoutBase):
    def test_the_backend_is_real_redis(self):
        self.assertIn("redis", settings.CACHES["default"]["BACKEND"].lower())
        self.assertTrue(cache.client.get_client().ping())

    def test_the_legacy_mechanism_really_is_disabled(self):
        cache.set("courses:user_id__sentinel:query__x", "cached", 300)
        self.teacher_a.first_name = "Trigger"
        self.teacher_a.save(update_fields=["first_name"])
        self.assertEqual(cache.get("courses:user_id__sentinel:query__x"), "cached")


class UserRowFreshnessTests(UserFanoutBase):
    """Every one of the 16 probe-STALE pairs, now required FRESH.

    Each check asserts two things: the uncached payload really changed
    (otherwise the test would pass trivially), and the cached read after the
    mutation equals it.
    """

    def endpoint(self, label):
        return {
            "23 summary": (self.admin_a, "/api/v1/school-admin/dashboard/summary"),
            "25 sa students": (self.admin_a, "/api/v1/school-admin/dashboard/students"),
            "30 teacher_performance": (
                self.admin_a,
                "/api/v1/school-admin/dashboard/teachers",
            ),
            "33 department_overview": (
                self.admin_a,
                "/api/v1/school-admin/dashboard/course-overview-chart",
            ),
            "29 ta students": (
                self.teacher_a,
                f"/api/v1/teacher-admin/dashboard/students/{self.course_a.id}",
            ),
            "course-list": (self.teacher_a, reverse("course-list")),
            "course-detail": (
                self.teacher_a,
                reverse("course-detail", args=[self.course_a.id]),
            ),
            "submission-list": (self.teacher_a, reverse("student-submission-list")),
        }[label]

    def assert_fresh_after(self, labels, mutate):
        clients = {}
        targets = {}
        for label in labels:
            user, url = self.endpoint(label)
            clients.setdefault(user.pk, self.client_for(user))
            targets[label] = (clients[user.pk], url)

        before = {}
        for label, (client, url) in targets.items():
            response = client.get(url)
            self.assertEqual(response.status_code, 200, (label, response.data))
            before[label] = response.data

        mutate()

        cached_after = {label: c.get(u).data for label, (c, u) in targets.items()}
        cache.clear()
        fresh = {label: c.get(u).data for label, (c, u) in targets.items()}

        for label in labels:
            with self.subTest(endpoint=label):
                self.assertNotEqual(
                    before[label],
                    fresh[label],
                    "the mutation does not change this payload - the check "
                    "would pass trivially",
                )
                self.assertEqual(
                    cached_after[label],
                    fresh[label],
                    f"{label} served a stale payload after the user-row change",
                )

    def test_m1_teacher_rename(self):
        def mutate():
            self.teacher_a.first_name = "Renamedteacher"
            self.teacher_a.save(update_fields=["first_name"])

        self.assert_fresh_after(["30 teacher_performance"], mutate)

    def test_m2_student_rename(self):
        def mutate():
            self.student.first_name = "Renamedstudent"
            self.student.save(update_fields=["first_name"])

        self.assert_fresh_after(
            ["29 ta students", "course-list", "course-detail", "submission-list"],
            mutate,
        )

    def test_m3_new_teacher_joins_the_school(self):
        self.assert_fresh_after(
            ["23 summary", "30 teacher_performance"],
            lambda: make_user("fo-new@x.test", UserTypes.TEACHER, self.school_a),
        )

    def test_m3_teacher_created_directly_in_the_school(self):
        """Single-save creation (created=True with the school already set)."""
        self.assert_fresh_after(
            ["23 summary", "30 teacher_performance"],
            lambda: User.objects.create_user(
                email="fo-new-direct@x.test",
                password="password123",  # nosec  # pragma: allowlist secret
                user_type=UserTypes.TEACHER,
                school=self.school_a,
                is_active=True,
            ),
        )

    def test_m4_teacher_moves_to_another_school(self):
        def mutate():
            self.teacher_a.school = self.school_c
            self.teacher_a.save(update_fields=["school"])

        self.assert_fresh_after(
            [
                "23 summary",
                "25 sa students",
                "30 teacher_performance",
                "33 department_overview",
            ],
            mutate,
        )

    def test_m5_student_deactivated(self):
        def mutate():
            self.student.is_active = False
            self.student.save(update_fields=["is_active"])

        self.assert_fresh_after(["23 summary", "course-list", "course-detail"], mutate)

    def test_m6_student_email_change(self):
        def mutate():
            self.student.email = "fo-s-new@x.test"
            self.student.save(update_fields=["email"])

        self.assert_fresh_after(["course-list", "course-detail"], mutate)

    def test_m2_through_a_full_save_without_update_fields(self):
        """`save()` with no update_fields is the admin/serializer path: the
        change is detected by comparing against the pre-save row."""

        def mutate():
            student = User.objects.get(pk=self.student.pk)
            student.last_name = "Fullsave"
            student.save()

        self.assert_fresh_after(["29 ta students", "course-list"], mutate)


class UserRowPrecisionTests(UserFanoutBase):
    """The half a freshness test cannot see: what must NOT move."""

    def test_student_rename_moves_only_their_teachers_and_those_schools(self):
        before = self.generations()
        self.student.first_name = "Precise"
        self.student.save(update_fields=["first_name"])

        self.assert_moved(before, "usr_student", "usr_teacher_a", "sch_a")
        self.assert_still(
            before,
            "usr_teacher_a2",
            "usr_teacher_b",
            "usr_admin_a",
            "usr_admin_b",
            "usr_bystander",
            "sch_b",
            "sch_c",
            "anysch",
            "crs_a",
        )

    def test_a_student_in_two_schools_moves_both_and_nothing_else(self):
        before = self.generations()
        self.bystander.first_name = "Twoschools"
        self.bystander.save(update_fields=["first_name"])

        self.assert_moved(before, "usr_teacher_a2", "usr_teacher_b", "sch_a", "sch_b")
        self.assert_still(before, "usr_teacher_a", "usr_student", "sch_c", "anysch")

    def test_teacher_rename_moves_their_school_only(self):
        before = self.generations()
        self.teacher_a.first_name = "Precise"
        self.teacher_a.save(update_fields=["first_name"])

        self.assert_moved(before, "usr_teacher_a", "sch_a")
        self.assert_still(
            before,
            "sch_b",
            "sch_c",
            "anysch",
            "usr_teacher_a2",
            "usr_teacher_b",
            "usr_student",
            "usr_admin_a",
        )

    def test_a_school_move_moves_the_old_and_new_school_only(self):
        before = self.generations()
        self.teacher_a.school = self.school_c
        self.teacher_a.save(update_fields=["school"])

        self.assert_moved(before, "sch_a", "sch_c")
        self.assert_still(before, "sch_b", "anysch", "usr_teacher_a2")

    def test_a_new_teacher_moves_their_school_only(self):
        before = self.generations()
        make_user("fo-new2@x.test", UserTypes.TEACHER, self.school_a)

        self.assert_moved(before, "sch_a")
        self.assert_still(before, "sch_b", "sch_c", "anysch", "usr_teacher_a")

    def test_deleting_a_student_reaches_their_teachers(self):
        """Carried by the CASCADE: the student's enrolments are deleted first
        and the StudentCourse receiver bumps each course's teacher and
        school. A separate pre_delete lookup was tried; removing it broke
        nothing, so it was dropped rather than kept as unproven code."""
        before = self.generations()
        User.objects.get(pk=self.student.pk).delete()

        self.assert_moved(before, "usr_teacher_a", "sch_a")
        self.assert_still(before, "usr_teacher_a2", "usr_teacher_b", "sch_b")

    def test_deleting_a_teacher_with_no_courses_reaches_their_school(self):
        """Nothing cascades for a course-less teacher, yet the school's
        teacher list and summary count them, so the delete itself must move
        the school."""
        lonely = make_user("fo-lonely@x.test", UserTypes.TEACHER, self.school_a)
        before = self.generations()

        lonely.delete()

        self.assert_moved(before, "sch_a")
        self.assert_still(before, "sch_b", "sch_c", "usr_teacher_a")

    def test_a_user_created_with_a_school_moves_that_school(self):
        """`create_user(..., school=...)` saves once, with created=True - the
        path an invitation or bulk import takes. (`make_user` sets the
        school in a SECOND save, so it never exercises this branch.)"""
        before = self.generations()

        User.objects.create_user(
            email="fo-direct@x.test",
            password="password123",  # nosec  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            school=self.school_a,
        )

        self.assert_moved(before, "sch_a")
        self.assert_still(before, "sch_b", "sch_c", "usr_teacher_a", "anysch")

    def test_invisible_field_changes_do_not_fan_out(self):
        """Nobody else displays these. Each still moves the user's own
        generation, as before - only the fan-out is withheld."""
        for update_fields, change in (
            (["bio"], lambda u: setattr(u, "bio", "private")),
            (
                ["failed_login_attempts", "locked_until"],
                lambda u: setattr(u, "failed_login_attempts", 2),
            ),
            (["password"], lambda u: u.set_password("n3w-Passw0rd!")),
        ):
            with self.subTest(update_fields=update_fields):
                before = self.generations()
                student = User.objects.get(pk=self.student.pk)
                change(student)
                student.save(update_fields=update_fields)

                self.assert_moved(before, "usr_student")
                self.assert_still(before, "usr_teacher_a", "sch_a", "sch_b")

    def test_a_full_save_with_no_visible_change_does_not_fan_out(self):
        student = User.objects.get(pk=self.student.pk)
        before = self.generations()
        student.bio = "changed but invisible"
        student.save()

        self.assert_still(before, "usr_teacher_a", "sch_a")

    def test_a_settings_save_does_not_fan_out(self):
        before = self.generations()
        settings_obj, _ = Settings.objects.get_or_create(user=self.student)
        settings_obj.save()

        self.assert_moved(before, "usr_student")
        self.assert_still(before, "usr_teacher_a", "sch_a")

    def test_the_other_tenant_dashboards_are_byte_identical(self):
        """Response-level isolation: school B's admin dashboards, warmed
        before a burst of school-A user changes, are served unchanged."""
        client_b = self.client_for(self.admin_b)
        urls = [
            "/api/v1/school-admin/dashboard/summary",
            "/api/v1/school-admin/dashboard/teachers",
            "/api/v1/school-admin/dashboard/course-overview-chart",
        ]
        warmed = {url: client_b.get(url).data for url in urls}
        before = self.generations()

        self.teacher_a.first_name = "Burst"
        self.teacher_a.save(update_fields=["first_name"])
        self.student.first_name = "Burst"
        self.student.save(update_fields=["first_name"])
        make_user("fo-burst@x.test", UserTypes.TEACHER, self.school_a)

        self.assert_still(before, "sch_b", "usr_admin_b", "usr_teacher_b")
        for url in urls:
            self.assertEqual(client_b.get(url).data, warmed[url], url)


class UserRowCostTests(UserFanoutBase):
    """Fan-out cost is O(1) queries and one Redis round trip per save."""

    def queries_for(self, update_fields, change):
        student = User.objects.get(pk=self.student.pk)
        change(student)
        with CaptureQueriesContext(connection) as ctx:
            student.save(update_fields=update_fields)
        return len(ctx.captured_queries)

    def test_the_fanout_query_count_is_flat_in_the_number_of_courses(self):
        small = self.queries_for(
            ["first_name"], lambda u: setattr(u, "first_name", "One")
        )

        for n in range(30):
            teacher = make_user(f"fo-many{n}@x.test", UserTypes.TEACHER, self.school_b)
            session = Session.objects.create(name=f"M{n}", teacher=teacher)
            course = Course.objects.create(
                name=f"M{n}", teacher=teacher, session=session
            )
            StudentCourse.objects.create(student=self.student, course=course)

        large = self.queries_for(
            ["first_name"], lambda u: setattr(u, "first_name", "Two")
        )
        self.assertEqual(large, small, "fan-out queries grew with course count")

    def test_an_invisible_change_costs_no_extra_queries(self):
        visible = self.queries_for(
            ["first_name"], lambda u: setattr(u, "first_name", "Vis")
        )
        invisible = self.queries_for(["bio"], lambda u: setattr(u, "bio", "x"))
        self.assertEqual(
            visible - invisible,
            2,
            "a visible change should cost exactly the pre-save read plus the "
            "teacher lookup; an invisible one neither",
        )

    def test_one_redis_round_trip_per_save(self):
        with patch(
            "users.signals.bump_many",
            wraps=__import__(
                "AutoGrader.cache_generation", fromlist=["bump_many"]
            ).bump_many,
        ) as spy:
            self.student.first_name = "Spy"
            self.student.save(update_fields=["first_name"])
        self.assertEqual(spy.call_count, 1)


class AdminBulkActionTests(UserFanoutBase):
    """The admin's bulk activate/deactivate uses `QuerySet.update()`, which
    fires no signal - neither mechanism saw it before this fix."""

    def run_action(self, action_name, users):
        from django.contrib import admin as django_admin
        from django.test import RequestFactory

        from users.admin import CustomUserAdmin

        model_admin = CustomUserAdmin(User, django_admin.site)
        request = RequestFactory().post("/admin/users/customuser/")
        with patch.object(CustomUserAdmin, "message_user"):
            getattr(model_admin, action_name)(
                request, User.objects.filter(pk__in=[u.pk for u in users])
            )

    def test_bulk_deactivation_reaches_the_teachers_roster(self):
        client = self.client_for(self.teacher_a)
        url = reverse("course-detail", args=[self.course_a.id])
        before = client.get(url).data

        self.run_action("deactivate_users", [self.student])

        cached_after = client.get(url).data
        cache.clear()
        fresh = client.get(url).data
        self.assertNotEqual(before, fresh, "the check would pass trivially")
        self.assertEqual(cached_after, fresh)

    def test_bulk_action_is_precise_and_costs_one_teacher_query(self):
        before = self.generations()
        with CaptureQueriesContext(connection) as ctx:
            self.run_action("activate_users", [self.student, self.bystander])
        course_lookups = [
            q for q in ctx.captured_queries if "classrooms_course" in q["sql"]
        ]
        self.assertEqual(len(course_lookups), 1)
        self.assert_moved(
            before, "usr_teacher_a", "usr_teacher_a2", "usr_teacher_b", "sch_a", "sch_b"
        )
        self.assert_still(before, "sch_c", "usr_admin_a", "anysch")


class UserRowFailureTests(UserFanoutBase):
    def test_a_redis_outage_never_fails_the_user_save(self):
        broken = RedisConnectionError("redis unreachable")
        with patch.object(cache.client, "get_client", side_effect=broken):
            self.student.first_name = "Offline"
            self.student.save(update_fields=["first_name"])
        self.assertEqual(User.objects.get(pk=self.student.pk).first_name, "Offline")


class UserRowConcurrencyTests(UserFanoutBase):
    def test_concurrent_student_renames_each_bump_the_shared_teacher(self):
        students = []
        for n in range(8):
            # Distinct names: a course rejects two enrolled students with the
            # same full name.
            s = make_user(
                f"fo-c{n}@x.test", UserTypes.STUDENT, None, f"Conc{n}", "Racer"
            )
            StudentCourse.objects.create(student=s, course=self.course_a)
            students.append(s)
        before = self.generations()
        barrier = threading.Barrier(len(students))
        errors = []

        def rename(student_id, n):
            try:
                barrier.wait(timeout=30)
                user = User.objects.get(pk=student_id)
                user.first_name = f"Racer{n}"
                user.save(update_fields=["first_name"])
            except Exception as exc:  # pragma: no cover - reported below
                errors.append(exc)
            finally:
                connection.close()

        threads = [
            threading.Thread(target=rename, args=(s.pk, n))
            for n, s in enumerate(students)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        self.assertEqual(errors, [])
        after = self.generations()
        self.assertGreaterEqual(
            after["usr_teacher_a"] - before["usr_teacher_a"],
            len(students),
            "a concurrent bump was lost",
        )
        self.assertEqual(after["sch_b"], before["sch_b"])
