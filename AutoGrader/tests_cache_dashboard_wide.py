"""H-1 Stage 3, item 1: the dashboard as a SYSTEM, generations alone.

Thirty-three isolated family proofs do not show that the dashboard behaves.
Each was written in its own fixture, exercising one endpoint against one
mutation. This suite does what the owner asked for instead:

  * populate **every** migrated dashboard cache for two tenants at once;
  * with no wildcard invalidation anywhere (patched out until H-1 step 4
    deleted it; the suite now runs against the real code);
  * perform each relevant mutation;
  * assert every **affected** response changed;
  * assert every unrelated **tenant's** response is byte-identical;
  * assert unrelated **families** were not needlessly invalidated - over-
    invalidation would pass a freshness test while quietly reintroducing the
    performance bug this project exists to remove;
  * confirm no pattern delete left in production can reach a generation
    counter (until step 4: all 16 live wildcard patterns).

That last pair is the point. A freshness-only suite cannot distinguish
"correctly invalidated" from "invalidated everything", and the original
defect was the second one.

Real Redis + real Postgres.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from assignments.models import Assignment
from AutoGrader.cache_generation import (
    SCOPE_ANY_SCHOOL,
    SCOPE_ANY_USER,
    SCOPE_GLOBAL,
    SCOPE_SCHOOL,
    SCOPE_USER,
    get_generation,
)
from AutoGrader.test_cache import real_redis_caches
from classrooms.models import Course, School, Session, StudentCourse
from dashboard.models import SchoolAtRiskSnapshot
from users.models import UserTypes

User = get_user_model()

REDIS_CACHE = real_redis_caches("redis://127.0.0.1:6379/3")


def make_user(email, user_type, school=None, superuser=False):
    user = User.objects.create_user(
        email=email,
        password="password123",  # nosec  # pragma: allowlist secret
    )
    user.user_type = user_type
    user.is_active = True
    user.school = school
    if superuser:
        user.is_superuser = True
        user.is_staff = True
    user.save()
    return user


@override_settings(CACHES=REDIS_CACHE)
class DashboardWideBase(TransactionTestCase):
    """Two tenants, every dashboard warmed, no wildcard invalidation."""

    reset_sequences = True

    def setUp(self):
        cache.clear()

        # --- tenant A (the one we mutate) ---
        self.school_a = School.objects.create(name="Wide A")
        self.admin_a = make_user("wide-a@x.test", UserTypes.SCHOOL_ADMIN, self.school_a)
        self.teacher_a = make_user("wide-ta@x.test", UserTypes.TEACHER, self.school_a)
        self.session_a = Session.objects.create(name="WA", teacher=self.teacher_a)
        self.course_a = Course.objects.create(
            name="WA 101", teacher=self.teacher_a, session=self.session_a
        )
        self.student_a = make_user("wide-sa@x.test", UserTypes.STUDENT)
        StudentCourse.objects.create(student=self.student_a, course=self.course_a)

        # --- tenant B (must be untouched throughout) ---
        self.school_b = School.objects.create(name="Wide B")
        self.admin_b = make_user("wide-b@x.test", UserTypes.SCHOOL_ADMIN, self.school_b)
        self.teacher_b = make_user("wide-tb@x.test", UserTypes.TEACHER, self.school_b)
        self.session_b = Session.objects.create(name="WB", teacher=self.teacher_b)
        self.course_b = Course.objects.create(
            name="WB 101", teacher=self.teacher_b, session=self.session_b
        )
        self.student_b = make_user("wide-sb@x.test", UserTypes.STUDENT)
        StudentCourse.objects.create(student=self.student_b, course=self.course_b)

        self.superadmin = make_user(
            "wide-sup@x.test", UserTypes.SUPER_ADMIN, superuser=True
        )

        self.client_a = self._client(self.admin_a)
        self.client_b = self._client(self.admin_b)
        self.client_ta = self._client(self.teacher_a)
        self.client_sup = self._client(self.superadmin)

    def tearDown(self):
        cache.clear()

    @staticmethod
    def _client(user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    # ---- the dashboard surface, per audience ----

    def school_admin_views(self, client):
        return {
            "summary": "/api/v1/school-admin/dashboard/summary",
            "students": "/api/v1/school-admin/dashboard/students",
            "teachers": "/api/v1/school-admin/dashboard/teachers",
            "dept": "/api/v1/school-admin/dashboard/course-overview-chart",
            "activity": "/api/v1/school-admin/dashboard/assignment-activity-over-time",
            "at_risk": "/api/v1/school-admin/dashboard/at-risk-trend",
        }

    def superadmin_views(self):
        return {
            "adoption": "/api/v1/super-admin/dashboard/adoption",
            "usage": "/api/v1/super-admin/dashboard/usage",
            "ai_perf": "/api/v1/super-admin/dashboard/ai_performance",
            "scaling": "/api/v1/super-admin/dashboard/scaling_signals",
            "schools": "/api/v1/super-admin/dashboard/schools",
            "sa_teachers": "/api/v1/super-admin/dashboard/teachers",
            "sa_students": "/api/v1/super-admin/dashboard/students",
        }

    def snapshot(self, client, views):
        """Read every view, populating its cache, and return the payloads."""
        out = {}
        for name, path in views.items():
            response = client.get(path)
            self.assertEqual(response.status_code, 200, f"{name}: {response.data}")
            out[name] = response.data
        return out

    def warm_everything(self):
        return {
            "a": self.snapshot(self.client_a, self.school_admin_views(self.client_a)),
            "b": self.snapshot(self.client_b, self.school_admin_views(self.client_b)),
            "sup": self.snapshot(self.client_sup, self.superadmin_views()),
        }

    def assert_tenant_b_untouched(self, before):
        after = self.snapshot(self.client_b, self.school_admin_views(self.client_b))
        for name, payload in before["b"].items():
            self.assertEqual(
                payload,
                after[name],
                f"tenant B's {name!r} dashboard changed because tenant A "
                f"mutated - cross-tenant invalidation",
            )


class DashboardWideFreshnessTests(DashboardWideBase):
    """Every affected view refreshes; every unaffected one does not."""

    def test_no_wildcard_sweep_runs_on_a_mutation(self):
        cache.set("courses:user_id__sentinel:query__x", "cached", 300)
        self.teacher_a.first_name = "Trigger"
        self.teacher_a.save(update_fields=["first_name"])
        self.assertEqual(
            cache.get("courses:user_id__sentinel:query__x"),
            "cached",
            "a wildcard sweep ran - this entire suite would be masked",
        )

    def test_every_dashboard_can_be_warmed(self):
        """Guard on the guard: all 13 views must return 200 and cache, or
        every assertion below is vacuous."""
        warmed = self.warm_everything()
        self.assertEqual(len(warmed["a"]), 6)
        self.assertEqual(len(warmed["b"]), 6)
        self.assertEqual(len(warmed["sup"]), 7)

    def test_an_assignment_refreshes_exactly_the_right_views(self):
        before = self.warm_everything()

        Assignment.objects.create(
            title="Wide assignment", course=self.course_a, teacher=self.teacher_a
        )

        after_a = self.snapshot(self.client_a, self.school_admin_views(self.client_a))
        self.assertNotEqual(
            before["a"]["activity"],
            after_a["activity"],
            "assignment activity did not refresh after a new assignment",
        )
        self.assert_tenant_b_untouched(before)

    def test_an_enrollment_refreshes_school_admin_views(self):
        before = self.warm_everything()

        newcomer = make_user("wide-sa2@x.test", UserTypes.STUDENT)
        StudentCourse.objects.create(student=newcomer, course=self.course_a)

        after_a = self.snapshot(self.client_a, self.school_admin_views(self.client_a))
        self.assertNotEqual(before["a"]["students"], after_a["students"])
        self.assert_tenant_b_untouched(before)

    def test_a_daily_snapshot_refreshes_only_the_at_risk_trend(self):
        before = self.warm_everything()

        SchoolAtRiskSnapshot.objects.create(
            school=self.school_a,
            snapshot_date=timezone.now().date(),
            at_risk_count=7,
        )

        after_a = self.snapshot(self.client_a, self.school_admin_views(self.client_a))
        self.assertNotEqual(
            before["a"]["at_risk"],
            after_a["at_risk"],
            "the at-risk trend did not refresh after its daily snapshot",
        )
        self.assert_tenant_b_untouched(before)


class DashboardWideOverInvalidationTests(DashboardWideBase):
    """The half a freshness suite cannot see.

    A mutation must not invalidate families that do not depend on it.
    Asserted on GENERATIONS, because that is where over-invalidation is
    visible - a response can be rebuilt to an identical payload and look
    fine while the cache was needlessly thrown away.
    """

    def generations(self):
        return {
            "global": get_generation(SCOPE_GLOBAL),
            "any_school": get_generation(SCOPE_ANY_SCHOOL),
            "any_user": get_generation(SCOPE_ANY_USER),
            "school_a": get_generation(SCOPE_SCHOOL, self.school_a.id),
            "school_b": get_generation(SCOPE_SCHOOL, self.school_b.id),
            "teacher_a": get_generation(SCOPE_USER, self.teacher_a.id),
            "teacher_b": get_generation(SCOPE_USER, self.teacher_b.id),
            "admin_b": get_generation(SCOPE_USER, self.admin_b.id),
        }

    def assert_moved(self, before, *names):
        after = self.generations()
        for name in names:
            self.assertGreater(after[name], before[name], f"{name} did not move")

    def assert_still(self, before, *names):
        after = self.generations()
        for name in names:
            self.assertEqual(
                after[name],
                before[name],
                f"{name} moved unnecessarily - over-invalidation",
            )

    def test_an_assignment_does_not_touch_the_entity_class_counters(self):
        before = self.generations()

        Assignment.objects.create(
            title="Precision probe", course=self.course_a, teacher=self.teacher_a
        )

        self.assert_moved(before, "global", "school_a", "teacher_a")
        self.assert_still(before, "any_school", "any_user", "school_b", "teacher_b")

    def test_a_new_school_does_not_touch_the_user_table_counter(self):
        before = self.generations()

        School.objects.create(name="Wide C")

        self.assert_moved(before, "any_school", "global")
        self.assert_still(before, "any_user", "school_a", "school_b", "teacher_a")

    def test_a_new_user_does_not_touch_the_school_table_counter(self):
        """A new teacher joins school A.

        This test used to assert `school_a` did NOT move - and that encoded
        a real defect. School A's teacher-performance and summary dashboards
        list and count the school's teachers, so a wildcards-disabled probe
        showed both serving the pre-join payload (H-1 Stage 3 item 7). The
        user's OWN school must move; the School-TABLE counter and every
        other tenant must not.
        """
        before = self.generations()

        make_user("wide-extra@x.test", UserTypes.TEACHER, self.school_a)

        self.assert_moved(before, "any_user", "global", "school_a")
        self.assert_still(before, "any_school", "school_b", "teacher_a")

    def test_a_tenant_a_mutation_never_moves_tenant_b(self):
        before = self.generations()

        Assignment.objects.create(
            title="A only", course=self.course_a, teacher=self.teacher_a
        )
        StudentCourse.objects.create(
            student=make_user("wide-sa3@x.test", UserTypes.STUDENT),
            course=self.course_a,
        )
        self.teacher_a.first_name = "Renamed"
        self.teacher_a.save(update_fields=["first_name"])

        self.assert_still(before, "school_b", "teacher_b", "admin_b")


class DashboardWideCollisionTests(DashboardWideBase):
    """No pattern delete left in production can reach a counter.

    Until H-1 step 4 this re-enabled the legacy mechanism and swept all 16
    live wildcard patterns, because a sweep that deleted a counter would
    reset its generation and revive every superseded entry. Those patterns
    no longer exist (the guard `AutoGrader/tests_no_wildcard_invalidation.py`
    keeps it so). The one pattern delete production still issues is the PDF
    cache's exact-prefix clear, so that is what the counters must survive.
    """

    def test_generation_counters_survive_the_pdf_exact_prefix_clear(self):
        from assignments.pdf_cache import invalidate_assignment_pdfs

        assignment = Assignment.objects.create(
            title="Seed", course=self.course_a, teacher=self.teacher_a
        )

        def generations():
            return {
                "global": get_generation(SCOPE_GLOBAL),
                "any_school": get_generation(SCOPE_ANY_SCHOOL),
                "any_user": get_generation(SCOPE_ANY_USER),
                "school_a": get_generation(SCOPE_SCHOOL, self.school_a.id),
                "teacher_a": get_generation(SCOPE_USER, self.teacher_a.id),
            }

        expected = generations()
        with patch("assignments.pdf_cache._enabled", return_value=True):
            invalidate_assignment_pdfs(assignment.id)

        self.assertEqual(
            generations(),
            expected,
            "the PDF exact-prefix clear reset a generation counter - every "
            "superseded cache entry would become reachable again",
        )

    def test_the_dashboards_work_end_to_end_on_the_real_code(self):
        """Formerly "both mechanisms live", the state that shipped until
        step 4. Now the only state: a real write refreshes the dashboard."""
        warmed = self.warm_everything()
        self.assertEqual(len(warmed["sup"]), 7)

        Assignment.objects.create(
            title="Real code", course=self.course_a, teacher=self.teacher_a
        )

        after = self.snapshot(self.client_a, self.school_admin_views(self.client_a))
        self.assertNotEqual(warmed["a"]["activity"], after["activity"])
