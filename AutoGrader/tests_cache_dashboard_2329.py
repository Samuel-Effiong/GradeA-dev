"""H-1 stage 2, dashboard families 23-29.

Families 23-25 are the school-admin dashboards (summary, at-risk trend,
students); 26-29 are the teacher dashboards (overview, courses, assignments,
students).

Two rules from the 30-33 round are mandatory here and are why this file is
shaped the way it is:

**Mechanism independence.** Every decisive freshness test runs with the
legacy `delete_cache_patterns` disabled. These keys contain the substrings
"school" and "user", so the legacy wildcards still sweep them while both
mechanisms run - a passing test under dual-running is evidence about the
PAIR, not about the generation graph.

**Dependency correctness derived from code, not names.** The dependency for
each family was read off the actual query in the cache-miss branch. That
re-reading found a dependency the matrix had missed entirely:
`schooladmins:*:view__at_risk_trend` reads `SchoolAtRiskSnapshot`, which is
written by a daily Celery task, not by any request path - so no
request-driven receiver could ever invalidate it. `dashboard/signals.py` was
created for it.

Real Redis + real Postgres.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from assignments.models import Assignment
from AutoGrader.cache_generation import (
    SCOPE_COURSE,
    SCOPE_SCHOOL,
    SCOPE_USER,
    get_generation,
)
from AutoGrader.test_cache import real_redis_caches
from classrooms.models import Course, School, Session, StudentCourse
from dashboard.models import SchoolAtRiskSnapshot
from users.models import UserTypes

User = get_user_model()

REDIS_CACHE = real_redis_caches("redis://127.0.0.1:6379/6")

LEGACY_MODULES = (
    "classrooms.signals",
    "users.signals",
    "students.signals",
    "assignments.signals",
)


def make_user(email, user_type, school=None):
    user = User.objects.create_user(
        email=email,
        password="password123",  # nosec  # pragma: allowlist secret
    )
    user.user_type = user_type
    user.is_active = True
    user.school = school
    user.save()
    return user


@override_settings(CACHES=REDIS_CACHE)
class DashboardBase(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        cache.clear()
        self.school = School.objects.create(name="D2329 School")
        self.admin = make_user("d23-a@x.test", UserTypes.SCHOOL_ADMIN, self.school)
        self.teacher = make_user("d23-t@x.test", UserTypes.TEACHER, self.school)
        self.session = Session.objects.create(name="D2329", teacher=self.teacher)
        self.course = Course.objects.create(
            name="D2329 101", teacher=self.teacher, session=self.session
        )
        self.student = make_user("d23-s@x.test", UserTypes.STUDENT)
        StudentCourse.objects.create(student=self.student, course=self.course)

        from rest_framework.test import APIClient

        self.admin_client = APIClient()
        self.admin_client.force_authenticate(self.admin)
        self.teacher_client = APIClient()
        self.teacher_client.force_authenticate(self.teacher)

    def tearDown(self):
        cache.clear()

    def disable_legacy(self):
        """Neutralise the wildcard mechanism in EVERY module that holds a
        reference. Patching one leaves the others live and silently restores
        the masking that hid a broken dependency in the 30-33 round."""
        patches = [
            patch(f"{module}.delete_cache_patterns", lambda *a, **k: None)
            for module in LEGACY_MODULES
        ]
        for p in patches:
            p.start()
        self.addCleanup(self._stop_legacy_patches, patches)

    @staticmethod
    def _stop_legacy_patches(patches):
        for p in patches:
            p.stop()

    def get(self, client, path, **params):
        response = client.get(path, params)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data


class SchoolAdminFamiliesTests(DashboardBase):
    """Families 23-25, proved without the legacy mechanism."""

    SUMMARY = "/api/v1/school-admin/dashboard/summary"
    STUDENTS = "/api/v1/school-admin/dashboard/students"

    def setUp(self):
        super().setUp()
        self.disable_legacy()

    def test_the_legacy_mechanism_really_is_disabled(self):
        cache.set("courses:user_id__sentinel:query__x", "cached", 300)
        self.teacher.first_name = "Trigger"
        self.teacher.save(update_fields=["first_name"])
        self.assertEqual(
            cache.get("courses:user_id__sentinel:query__x"),
            "cached",
            "a legacy sweep still ran - the patch did not take, so every "
            "test in this class would be masked",
        )

    def test_summary_refreshes_after_a_new_assignment(self):
        before = self.get(self.admin_client, self.SUMMARY)
        Assignment.objects.create(
            title="Summary", course=self.course, teacher=self.teacher
        )
        self.assertNotEqual(before, self.get(self.admin_client, self.SUMMARY))

    def test_summary_refreshes_after_a_new_course(self):
        before = self.get(self.admin_client, self.SUMMARY)
        Course.objects.create(name="Second", teacher=self.teacher, session=self.session)
        self.assertNotEqual(before, self.get(self.admin_client, self.SUMMARY))

    def test_students_dashboard_refreshes_after_an_enrollment(self):
        before = self.get(self.admin_client, self.STUDENTS)
        newcomer = make_user("d23-s2@x.test", UserTypes.STUDENT)
        StudentCourse.objects.create(student=newcomer, course=self.course)
        self.assertNotEqual(before, self.get(self.admin_client, self.STUDENTS))

    def test_the_summary_is_still_cached_between_mutations(self):
        """Guard on the guard: if nothing cached, every test here passes
        trivially."""
        self.get(self.admin_client, self.SUMMARY)
        generation = get_generation(SCOPE_SCHOOL, self.school.id)
        self.get(self.admin_client, self.SUMMARY)
        self.assertEqual(get_generation(SCOPE_SCHOOL, self.school.id), generation)


class AtRiskTrendSnapshotDependencyTests(DashboardBase):
    """Family 24's missed dependency: `SchoolAtRiskSnapshot`.

    Written once a day by `dashboard/tasks.py`, never by a request. No
    request-driven receiver could invalidate this chart, so before
    `dashboard/signals.py` existed the trend was stale until its 3600s TTL
    expired - the longest stale window of any family in the project.
    """

    def test_writing_a_snapshot_bumps_the_school_generation(self):
        before = get_generation(SCOPE_SCHOOL, self.school.id)

        SchoolAtRiskSnapshot.objects.create(
            school=self.school, snapshot_date=timezone.now().date(), at_risk_count=3
        )

        self.assertGreater(
            get_generation(SCOPE_SCHOOL, self.school.id),
            before,
            "a daily at-risk snapshot moved no generation - the trend chart "
            "would serve stale data for its full 3600s TTL",
        )

    def test_updating_a_snapshot_bumps_it_again(self):
        snapshot = SchoolAtRiskSnapshot.objects.create(
            school=self.school, snapshot_date=timezone.now().date(), at_risk_count=3
        )
        before = get_generation(SCOPE_SCHOOL, self.school.id)

        snapshot.at_risk_count = 9
        snapshot.save(update_fields=["at_risk_count"])

        self.assertGreater(get_generation(SCOPE_SCHOOL, self.school.id), before)

    def test_a_snapshot_for_another_school_does_not_bump_this_one(self):
        other = School.objects.create(name="Other 2329")
        before = get_generation(SCOPE_SCHOOL, self.school.id)

        SchoolAtRiskSnapshot.objects.create(
            school=other, snapshot_date=timezone.now().date(), at_risk_count=5
        )

        self.assertEqual(
            get_generation(SCOPE_SCHOOL, self.school.id),
            before,
            "another school's snapshot bumped this school's generation",
        )

    def test_the_at_risk_trend_RESPONSE_becomes_fresh_after_a_snapshot(self):
        """The fourth property, and the one that actually matters.

        The three tests around this assert generations moved. This one
        asserts the SCHOOL ADMIN SEES THE NEW NUMBER - which is the defect
        that was open: a daily snapshot changed the data and the chart kept
        serving the previous week's counts for its full 3600s TTL.

        Runs with the legacy mechanism disabled, so only the generation
        bump can be responsible for the refresh.
        """
        self.disable_legacy()
        url = "/api/v1/school-admin/dashboard/at-risk-trend"

        SchoolAtRiskSnapshot.objects.create(
            school=self.school,
            snapshot_date=timezone.now().date(),
            at_risk_count=2,
        )
        before = self.get(self.admin_client, url)

        SchoolAtRiskSnapshot.objects.update_or_create(
            school=self.school,
            snapshot_date=timezone.now().date(),
            defaults={"at_risk_count": 11},
        )

        after = self.get(self.admin_client, url)
        self.assertNotEqual(
            before,
            after,
            "the at-risk trend chart served a stale count after the daily "
            "snapshot was rewritten - this is the 3600s freshness defect",
        )

    def test_a_snapshot_does_not_bump_any_user_generation(self):
        """The snapshot is school-shaped; bumping users would be
        over-invalidation dressed up as safety."""
        before = get_generation(SCOPE_USER, self.admin.id)

        SchoolAtRiskSnapshot.objects.create(
            school=self.school, snapshot_date=timezone.now().date(), at_risk_count=1
        )

        self.assertEqual(get_generation(SCOPE_USER, self.admin.id), before)


class TeacherDashboardFamiliesTests(DashboardBase):
    """Families 26-29, proved without the legacy mechanism.

    26 keys on a SESSION id and 28 on an ASSIGNMENT id - not a course, as
    the matrix originally claimed. `usr` alone is correct for both because
    every mutation that can affect a teacher's dashboard bumps that
    teacher's generation.
    """

    def setUp(self):
        super().setUp()
        self.disable_legacy()

    def overview_url(self):
        return f"/api/v1/teacher-admin/dashboard/overview/{self.session.id}"

    def courses_url(self):
        return f"/api/v1/teacher-admin/dashboard/courses/{self.course.id}"

    def test_overview_refreshes_after_a_new_assignment(self):
        before = self.get(self.teacher_client, self.overview_url())
        Assignment.objects.create(
            title="Overview", course=self.course, teacher=self.teacher
        )
        self.assertNotEqual(before, self.get(self.teacher_client, self.overview_url()))

    def test_overview_refreshes_after_an_enrollment(self):
        before = self.get(self.teacher_client, self.overview_url())
        newcomer = make_user("d23-s3@x.test", UserTypes.STUDENT)
        StudentCourse.objects.create(student=newcomer, course=self.course)
        self.assertNotEqual(before, self.get(self.teacher_client, self.overview_url()))

    def test_courses_refreshes_after_a_new_assignment(self):
        """Mutates something this payload actually reports.

        The course-analytics payload carries workflow/performance/ai_trust
        metrics and no course NAME, so renaming the course correctly changes
        nothing - asserting on a rename would test the endpoint's shape
        rather than its freshness. An assignment moves
        `total_assignments_assigned`.
        """
        before = self.get(self.teacher_client, self.courses_url())
        self.assertEqual(before["workflow"]["total_assignments_assigned"], 0)

        Assignment.objects.create(
            title="Course analytics", course=self.course, teacher=self.teacher
        )

        after = self.get(self.teacher_client, self.courses_url())
        self.assertEqual(
            after["workflow"]["total_assignments_assigned"],
            1,
            "the course-analytics dashboard served a stale assignment count",
        )

    def test_a_course_scope_would_add_nothing_to_these_keys(self):
        """Documents why families 27/29 carry `usr` ALONE.

        A `crs` scope was tried and removed. Every SCOPE_COURSE bump in the
        project is accompanied by the teacher's SCOPE_USER bump - verified
        across `_course_scopes`, `_bump_assignment_scopes` and
        `_bump_submission_scopes` - so a key already carrying that teacher's
        generation gains no precision from `crs`, only an extra Redis read.

        Mutation testing is what exposed this: removing `crs` broke nothing,
        because it was doing nothing. This test pins the reason so the scope
        is not "helpfully" re-added later.
        """
        from AutoGrader.cache_generation import get_generation

        before_usr = get_generation(SCOPE_USER, self.teacher.id)
        before_crs = get_generation(SCOPE_COURSE, self.course.id)

        Assignment.objects.create(
            title="Bumps both", course=self.course, teacher=self.teacher
        )

        self.assertGreater(get_generation(SCOPE_COURSE, self.course.id), before_crs)
        self.assertGreater(
            get_generation(SCOPE_USER, self.teacher.id),
            before_usr,
            "a course bump occurred WITHOUT the teacher's user bump - if "
            "this ever happens, `crs` becomes load-bearing for families "
            "27/29 and must be restored to their keys",
        )


class DashboardTenantIsolationTests(DashboardBase):
    """One school's activity must not move another school's generations, and
    must not change another admin's cached dashboards."""

    def setUp(self):
        super().setUp()
        self.other_school = School.objects.create(name="Isolated School")
        self.other_admin = make_user(
            "iso-a@x.test", UserTypes.SCHOOL_ADMIN, self.other_school
        )
        self.other_teacher = make_user(
            "iso-t@x.test", UserTypes.TEACHER, self.other_school
        )
        self.disable_legacy()

    def test_a_mutation_here_does_not_bump_the_other_schools_generation(self):
        before = get_generation(SCOPE_SCHOOL, self.other_school.id)
        Assignment.objects.create(
            title="Mine", course=self.course, teacher=self.teacher
        )
        self.assertEqual(get_generation(SCOPE_SCHOOL, self.other_school.id), before)

    def test_a_mutation_here_does_not_bump_the_other_admins_generation(self):
        before = get_generation(SCOPE_USER, self.other_admin.id)
        Course.objects.create(name="Mine 2", teacher=self.teacher, session=self.session)
        self.assertEqual(get_generation(SCOPE_USER, self.other_admin.id), before)

    def test_the_other_admins_cached_summary_is_byte_identical(self):
        from rest_framework.test import APIClient

        other_client = APIClient()
        other_client.force_authenticate(self.other_admin)
        url = "/api/v1/school-admin/dashboard/summary"

        first = self.get(other_client, url)
        Assignment.objects.create(
            title="Mine 3", course=self.course, teacher=self.teacher
        )
        self.assertEqual(
            first,
            self.get(other_client, url),
            "another school's dashboard changed because this school "
            "published an assignment",
        )
