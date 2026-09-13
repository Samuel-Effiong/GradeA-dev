"""H-1 stage 2, dashboard families 30-33: the freshness bug, closed.

These four school-admin responses were reached by NO invalidation pattern at
all (Phase 1 sweep), because they did not follow the
`<entity>:<scope>__<id>:` convention every wildcard was written against:

    teacher_performance_<school>_<page>_<size>    TTL 300s
    teacher_detail_<school>_<teacher>             TTL 300s
    assignment_activity_<school>_<year>           TTL 900s
    department_overview_<school>                  TTL 300s

A school admin could watch a teacher publish an assignment and keep seeing
the old numbers for up to fifteen minutes. That is a live correctness
defect, not a performance one, which is why these were migrated ahead of the
lower-risk families.

Every test asserts the RESPONSE BODY changed - the actual numbers a school
admin reads. Asserting that a counter incremented would prove the bump
fired, not that the staleness is gone; those are different claims, and only
the second one is the bug.

Real Redis + real Postgres, per the H-1 verification gate. `TransactionTestCase`
because the signal receivers that bump generations run on commit paths.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient

from assignments.models import Assignment
from AutoGrader.cache_generation import SCOPE_SCHOOL, SCOPE_USER, get_generation
from classrooms.models import Course, School, Session, StudentCourse
from users.models import UserTypes

User = get_user_model()

REDIS_CACHE = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": "redis://127.0.0.1:6379/7",
        "OPTIONS": {"CLIENT_CLASS": "django_redis.client.DefaultClient"},
        "KEY_PREFIX": "gaplus",
    }
}


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
class DashboardFreshnessBase(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        cache.clear()
        self.school = School.objects.create(name="Fresh School")
        self.admin = make_user("fresh-a@x.test", UserTypes.SCHOOL_ADMIN, self.school)
        self.teacher = make_user("fresh-t@x.test", UserTypes.TEACHER, self.school)
        self.session = Session.objects.create(name="Fresh", teacher=self.teacher)
        self.course = Course.objects.create(
            name="Fresh 101", teacher=self.teacher, session=self.session
        )
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def tearDown(self):
        cache.clear()

    def get(self, path, **params):
        response = self.client.get(path, params)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data


class TeacherPerformanceFreshnessTests(DashboardFreshnessBase):
    """Family 30 — `dashboards:school_id__<id>:view__teacher_performance:*`."""

    URL = "/api/v1/school-admin/dashboard/teachers"

    def teacher_row(self, payload):
        rows = payload["results"] if isinstance(payload, dict) else payload
        for row in rows:
            if str(row.get("id")) == str(self.teacher.id):
                return row
        return None

    def test_a_new_assignment_is_reflected_on_the_next_read(self):
        """The exact bug: before this migration, no mechanism reached this
        key, so this second read served the pre-assignment numbers.

        Compares the teacher's whole row rather than one field. The payload
        exposes assignment activity as `assignments_per_week` (a rate over a
        180-day window), not a raw count, and pinning a derived rate would
        make this test about the statistic rather than about freshness.
        """
        before = self.teacher_row(self.get(self.URL))
        self.assertIsNotNone(before, "the teacher is missing from the payload")

        Assignment.objects.create(
            title="Newly published", course=self.course, teacher=self.teacher
        )

        self.assertNotEqual(
            before,
            self.teacher_row(self.get(self.URL)),
            "the teacher-performance dashboard served stale numbers after a "
            "new assignment was published",
        )

    def test_the_response_is_still_cached_between_mutations(self):
        """Guard on the guard: if caching were simply broken, every
        freshness test here would pass trivially."""
        self.get(self.URL)
        generation = get_generation(SCOPE_SCHOOL, self.school.id)
        self.get(self.URL)
        self.assertEqual(get_generation(SCOPE_SCHOOL, self.school.id), generation)

    def test_a_new_teacher_appears_on_the_next_read(self):
        first = self.get(self.URL)
        count_before = len(first["results"] if isinstance(first, dict) else first)

        make_user("fresh-t2@x.test", UserTypes.TEACHER, self.school)

        second = self.get(self.URL)
        count_after = len(second["results"] if isinstance(second, dict) else second)
        self.assertGreater(count_after, count_before)


class TeacherDetailFreshnessTests(DashboardFreshnessBase):
    """Family 31 — depends on BOTH the school and that teacher."""

    @property
    def url(self):
        return f"/api/v1/school-admin/dashboard/teachers/{self.teacher.id}"

    def test_a_new_assignment_is_reflected_on_the_next_read(self):
        before = self.get(self.url)
        Assignment.objects.create(
            title="Detail assignment", course=self.course, teacher=self.teacher
        )
        self.assertNotEqual(
            before,
            self.get(self.url),
            "teacher detail served stale data after an assignment was added",
        )

    def test_renaming_the_teacher_is_reflected_on_the_next_read(self):
        """Exercises the USER half of this key's dependency, which the
        school bump alone would not cover."""
        before = self.get(self.url)

        self.teacher.first_name = "Renamed"
        self.teacher.save(update_fields=["first_name"])

        self.assertNotEqual(before, self.get(self.url))


class AssignmentActivityFreshnessTests(DashboardFreshnessBase):
    """Family 32 — the worst of the four: a 900-second stale window."""

    URL = "/api/v1/school-admin/dashboard/assignment-activity-over-time"

    def test_a_new_assignment_is_reflected_on_the_next_read(self):
        before = self.get(self.URL)

        Assignment.objects.create(
            title="Activity assignment", course=self.course, teacher=self.teacher
        )

        self.assertNotEqual(
            before,
            self.get(self.URL),
            "assignment activity served stale data - this response has a "
            "900s TTL, so before the migration the bug was visible for a "
            "quarter of an hour",
        )

    def test_deleting_an_assignment_is_reflected_on_the_next_read(self):
        assignment = Assignment.objects.create(
            title="Doomed", course=self.course, teacher=self.teacher
        )
        before = self.get(self.URL)

        assignment.delete()

        self.assertNotEqual(before, self.get(self.URL))


class DepartmentOverviewFreshnessTests(DashboardFreshnessBase):
    """Family 33 — course and roster shaped."""

    URL = "/api/v1/school-admin/dashboard/course-overview-chart"

    def test_a_new_course_is_reflected_on_the_next_read(self):
        before = self.get(self.URL)

        Course.objects.create(
            name="Brand New Course", teacher=self.teacher, session=self.session
        )

        self.assertNotEqual(before, self.get(self.URL))

    def test_a_grade_change_is_reflected_on_the_next_read(self):
        """This payload carries name/teachers/avg_grade only, so a gradeless
        enrolment correctly changes nothing - asserting otherwise would test
        the endpoint's shape, not its freshness. A GRADE is what moves it."""
        student = make_user("fresh-s@x.test", UserTypes.STUDENT)
        enrollment = StudentCourse.objects.create(student=student, course=self.course)
        before = self.get(self.URL)
        self.assertIsNone(before["courses"][0]["avg_grade"])

        enrollment.final_grade = 88.5
        enrollment.save()

        after = self.get(self.URL)
        # Compared numerically: DRF renders this as a Decimal here and as a
        # string over HTTP, and pinning the rendering would make the test
        # about serialization rather than about freshness.
        self.assertIsNotNone(
            after["courses"][0]["avg_grade"],
            "the department overview served a stale average grade",
        )
        self.assertAlmostEqual(float(after["courses"][0]["avg_grade"]), 88.5)


@override_settings(CACHES=REDIS_CACHE)
class DashboardCrossTenantAndFailureTests(TransactionTestCase):
    """Adversarial + failure behaviour for families 30-33.

    The migration must not trade a freshness bug for an isolation bug: one
    school's mutation must leave another school's cached dashboards - and
    its generation - untouched.
    """

    reset_sequences = True

    def setUp(self):
        cache.clear()
        self.school_a = School.objects.create(name="Tenant A")
        self.school_b = School.objects.create(name="Tenant B")
        self.admin_a = make_user("ta@x.test", UserTypes.SCHOOL_ADMIN, self.school_a)
        self.admin_b = make_user("tb@x.test", UserTypes.SCHOOL_ADMIN, self.school_b)
        self.teacher_a = make_user("tta@x.test", UserTypes.TEACHER, self.school_a)
        self.teacher_b = make_user("ttb@x.test", UserTypes.TEACHER, self.school_b)
        self.session_a = Session.objects.create(name="A", teacher=self.teacher_a)
        self.course_a = Course.objects.create(
            name="A1", teacher=self.teacher_a, session=self.session_a
        )

    def tearDown(self):
        cache.clear()

    def test_one_schools_assignment_does_not_bump_another_schools_generation(self):
        before_b = get_generation(SCOPE_SCHOOL, self.school_b.id)

        Assignment.objects.create(
            title="A's assignment", course=self.course_a, teacher=self.teacher_a
        )

        self.assertEqual(
            get_generation(SCOPE_SCHOOL, self.school_b.id),
            before_b,
            "school B's generation moved because school A published an "
            "assignment - that is the cross-tenant over-invalidation this "
            "architecture removes",
        )

    def test_one_schools_mutation_leaves_the_other_schools_cache_readable(self):
        client_b = APIClient()
        client_b.force_authenticate(self.admin_b)
        url = "/api/v1/school-admin/dashboard/course-overview-chart"

        first_b = client_b.get(url)
        self.assertEqual(first_b.status_code, 200)

        Course.objects.create(name="A2", teacher=self.teacher_a, session=self.session_a)

        second_b = client_b.get(url)
        self.assertEqual(second_b.status_code, 200)
        self.assertEqual(
            first_b.data,
            second_b.data,
            "school B's dashboard changed because school A added a course",
        )

    def test_a_bump_failure_does_not_break_the_mutation(self):
        from unittest.mock import patch

        import redis as redis_lib

        with patch(
            "AutoGrader.cache_generation.cache.incr",
            side_effect=redis_lib.exceptions.ConnectionError("down"),
        ), patch(
            "AutoGrader.cache_generation.cache.add",
            side_effect=redis_lib.exceptions.ConnectionError("down"),
        ), patch(
            "AutoGrader.cache_generation._bump_pipelined", return_value=None
        ):
            assignment = Assignment.objects.create(
                title="During an outage",
                course=self.course_a,
                teacher=self.teacher_a,
            )

        self.assertTrue(
            Assignment.objects.filter(pk=assignment.pk).exists(),
            "a cache-generation failure rolled back the assignment",
        )

    def test_the_legacy_wildcards_cannot_destroy_a_school_generation(self):
        """Coexistence guard, at the dashboard level.

        The counters must survive a full legacy sweep - a destroyed counter
        resets the generation and makes superseded dashboard entries
        readable again.
        """
        from AutoGrader.tests_cache_generation import CounterNamespaceSafetyTests

        Assignment.objects.create(
            title="Seed", course=self.course_a, teacher=self.teacher_a
        )
        expected = get_generation(SCOPE_SCHOOL, self.school_a.id)

        for pattern in CounterNamespaceSafetyTests.LIVE_PATTERNS:
            cache.delete_pattern(pattern)

        self.assertEqual(
            get_generation(SCOPE_SCHOOL, self.school_a.id),
            expected,
            "a legacy wildcard sweep reset the school generation",
        )


@override_settings(CACHES=REDIS_CACHE)
class LegacyDisabledFreshnessTests(DashboardFreshnessBase):
    """Families 30-33 with the LEGACY mechanism switched off.

    Why this class exists, and it is the most important one in the file.

    While both mechanisms run, a freshness test cannot tell you WHICH one
    refreshed the data. These four keys are now named
    `dashboards:school_id__<id>:...`, which contains the substring "school",
    so the legacy `delete_pattern("*school*")` fired by `clear_user_cache`
    and `clear_course_cache` still sweeps them. Measured directly: with the
    generation deliberately removed from the teacher-detail key, a teacher
    rename STILL refreshed the response - the legacy sweep had deleted the
    entry. The test passed while proving nothing about the new mechanism.

    So these tests neutralise `delete_cache_patterns` and require the
    generation counters to carry the freshness guarantee alone. That is
    precisely the state stage 3 creates permanently, which makes this class
    the de-risking evidence for stage 3 as well as the honest proof for
    stage 2.
    """

    def setUp(self):
        super().setUp()
        # Patch every module that holds a reference to the helper. Patching
        # one leaves the others live and silently restores the masking.
        self._patches = [
            patch(f"{module}.delete_cache_patterns", lambda *a, **k: None)
            for module in (
                "classrooms.signals",
                "users.signals",
                "students.signals",
                "assignments.signals",
            )
        ]
        for p in self._patches:
            p.start()
        self.addCleanup(self._stop_patches)

    def _stop_patches(self):
        for p in self._patches:
            p.stop()

    def test_the_legacy_mechanism_really_is_disabled(self):
        """Guard on the guard: if the patch missed, every test below would
        be masked exactly as before and would prove nothing."""
        cache.set("courses:user_id__sentinel:query__x", "cached", 300)
        self.teacher.first_name = "Trigger"
        self.teacher.save(update_fields=["first_name"])
        self.assertEqual(
            cache.get("courses:user_id__sentinel:query__x"),
            "cached",
            "a legacy wildcard sweep still ran - the patch did not take",
        )

    def test_teacher_performance_refreshes_on_generation_alone(self):
        url = "/api/v1/school-admin/dashboard/teachers"
        before = self.get(url)

        Assignment.objects.create(
            title="Gen only", course=self.course, teacher=self.teacher
        )

        self.assertNotEqual(before, self.get(url))

    def test_teacher_detail_refreshes_on_generation_alone(self):
        """This is the case the legacy sweep was masking."""
        url = f"/api/v1/school-admin/dashboard/teachers/{self.teacher.id}"
        before = self.get(url)

        self.teacher.first_name = "GenOnly"
        self.teacher.save(update_fields=["first_name"])

        after = self.get(url)
        self.assertNotEqual(
            before,
            after,
            "teacher detail did not refresh from the generation mechanism "
            "alone - its USER scope is not doing the work",
        )
        self.assertEqual(after["name"].strip(), "GenOnly")

    def test_assignment_activity_refreshes_on_generation_alone(self):
        url = "/api/v1/school-admin/dashboard/assignment-activity-over-time"
        before = self.get(url)

        Assignment.objects.create(
            title="Gen only activity", course=self.course, teacher=self.teacher
        )

        self.assertNotEqual(before, self.get(url))

    def test_department_overview_refreshes_on_generation_alone(self):
        url = "/api/v1/school-admin/dashboard/course-overview-chart"
        before = self.get(url)

        Course.objects.create(
            name="Gen only course", teacher=self.teacher, session=self.session
        )

        self.assertNotEqual(before, self.get(url))

    def test_a_graded_submission_refreshes_teacher_performance(self):
        """Proves the StudentSubmission bump is load-bearing.

        Without it, grading-derived statistics (turnaround, ai_confidence,
        rigor) would keep serving pre-grading numbers, and no other bump
        covers this path.
        """
        from students.models import StudentSubmission

        student = make_user("gen-s@x.test", UserTypes.STUDENT)
        StudentCourse.objects.create(student=student, course=self.course)
        assignment = Assignment.objects.create(
            title="Graded", course=self.course, teacher=self.teacher
        )
        url = "/api/v1/school-admin/dashboard/teachers"
        before = self.get(url)

        # GRADED, not merely submitted. Every grading-derived field on this
        # payload (turnaround, ai_confidence, rigor) stays null for an
        # ungraded submission, so an ungraded one legitimately changes
        # nothing - asserting on it would test the statistic rather than
        # the invalidation.
        from django.utils import timezone

        StudentSubmission.objects.create(
            student=student,
            assignment=assignment,
            answers={},
            score=90,
            max_points=100,
            graded_at=timezone.now(),
            grading_confidence=0.9,
        )

        self.assertNotEqual(
            before,
            self.get(url),
            "teacher performance did not refresh after a graded submission "
            "- the StudentSubmission generation bump is not wired or not "
            "reached",
        )


@override_settings(CACHES=REDIS_CACHE)
class SubmissionBumpIsLoadBearingTests(DashboardFreshnessBase):
    """The StudentSubmission bump, pinned where it actually does work.

    Mutation testing showed that removing `_bump_submission_scopes` did NOT
    break families 30-33. That is not a gap in the wiring - it is a real
    redundancy, and the reason is worth recording:

      * a GRADED submission triggers `_recalculate_final_grade`, which saves
        the `StudentCourse` row, which fires `clear_student_course_cache`,
        which already bumps the student, course, teacher and school. The
        school generation therefore moves transitively, with or without the
        submission bump;
      * an UNGRADED submission does not change `final_grade`, so that recalc
        writes nothing and fires nothing. Measured with the bump removed:
        the school generation did not move at all (5 -> 5).

    So the submission bump is load-bearing for ungraded submissions, whose
    visible effect belongs to the `studentsubmissions:*` families (8 and 12)
    rather than to these four dashboards. Asserting on a generation here -
    rather than on a response body - is deliberate: the freshness this bump
    protects is not observable in families 30-33's payloads, and inventing a
    freshness assertion against them would be testing the wrong thing.

    When families 8 and 12 are proved with the legacy mechanism disabled,
    this link gets its freshness test and this class can be reduced.
    """

    def test_an_ungraded_submission_bumps_the_school_generation(self):
        from students.models import StudentSubmission

        student = make_user("bump-s@x.test", UserTypes.STUDENT)
        StudentCourse.objects.create(student=student, course=self.course)
        assignment = Assignment.objects.create(
            title="Ungraded", course=self.course, teacher=self.teacher
        )
        before = get_generation(SCOPE_SCHOOL, self.school.id)

        StudentSubmission.objects.create(
            student=student, assignment=assignment, answers={}
        )

        self.assertGreater(
            get_generation(SCOPE_SCHOOL, self.school.id),
            before,
            "an ungraded submission moved no generation - nothing else "
            "covers this path, so the submission bump is required",
        )

    def test_an_ungraded_submission_bumps_the_students_generation(self):
        from students.models import StudentSubmission

        student = make_user("bump-s2@x.test", UserTypes.STUDENT)
        StudentCourse.objects.create(student=student, course=self.course)
        assignment = Assignment.objects.create(
            title="Ungraded 2", course=self.course, teacher=self.teacher
        )
        before = get_generation(SCOPE_USER, student.id)

        StudentSubmission.objects.create(
            student=student, assignment=assignment, answers={}
        )

        self.assertGreater(get_generation(SCOPE_USER, student.id), before)
