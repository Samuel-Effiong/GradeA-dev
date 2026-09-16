"""H-1 Stage 3, gap G4: a school admin's cached view of a user outlives a
school move.

`UserCacheMixin.get_cache_key` scopes a `retrieve` read by the REQUESTING
user's own generation only (`SCOPE_USER, user_id`). When a teacher moves to
another school, `_viewer_scopes_for_signal` (users/signals.py) bumps
`SCOPE_SCHOOL` for the old and new school -- correct for dashboards keyed on
the school -- but nothing bumps the viewing school admin's OWN `usr`
generation, so their cached `GET users/<teacher.pk>` keeps serving a 200 for
a teacher who has since left their school and dropped out of their
queryset entirely (`get_queryset` filters school admins to
`Q(school_id=user.school_id)`).

Fixtures follow the Stage 3 rule (plan §0): rows are created directly, with
exactly the fields production sets (`CustomUser.school` is written by both
`create_with_admin` and `add_teachers`); the mutation under test runs
through the real `PATCH users/<pk>` endpoint, which only a superadmin may
use to move a user between schools.

Real Redis + real Postgres.
"""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TransactionTestCase
from django.urls import reverse
from rest_framework.test import APIClient

from AutoGrader.tests_cache_matrix_support import (
    STALE,
    FreshnessMatrixMixin,
    Read,
    legacy_wildcards_disabled,
)
from classrooms.models import School
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


class SchoolAdminUserViewFreshnessTests(FreshnessMatrixMixin, TransactionTestCase):
    """G4: a school admin's cached view of a teacher must drop with them."""

    reset_sequences = True

    def setUp(self):
        cache.clear()
        self.patched_modules = self.enterContext(legacy_wildcards_disabled())
        self.assertTrue(self.patched_modules, "no legacy module was patched")

        self.school_a = School.objects.create(name="G4 School A")
        self.school_b = School.objects.create(name="G4 School B")

        self.admin = make_active_user(
            "g4-admin@x.test",
            UserTypes.SCHOOL_ADMIN,
            "G4Admin",
            school=self.school_a,
        )
        self.teacher = make_active_user(
            "g4-teacher@x.test",
            UserTypes.TEACHER,
            "G4Teacher",
            school=self.school_a,
        )
        self.superadmin = make_active_user(
            "g4-super@x.test",
            UserTypes.SUPER_ADMIN,
            "G4Super",
            is_superuser=True,
        )

        self.teacher_url = reverse("user-detail", args=[self.teacher.pk])

    def reads(self):
        return [
            Read("school admin's view of the teacher", self.admin, self.teacher_url)
        ]

    def move_teacher_to_school_b(self):
        client = APIClient()
        client.force_authenticate(self.superadmin)
        response = client.patch(
            self.teacher_url, {"school": str(self.school_b.pk)}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.content)

    def test_school_move_currently_leaves_the_admins_cached_view_stale(self):
        result = self.run_matrix(
            "move teacher to another school (G4 gap, no admin usr bump)",
            self.reads(),
            self.move_teacher_to_school_b,
        )
        verdicts = {o.label: o.verdict for o in result.outcomes}
        self.assertEqual(
            verdicts,
            {"school admin's view of the teacher": STALE},
            result.table(),
        )
        stale = result.outcomes[0]
        self.assertEqual(stale.cached_after[0], 200, result.table())
        self.assertEqual(stale.truth_after[0], 404, result.table())
