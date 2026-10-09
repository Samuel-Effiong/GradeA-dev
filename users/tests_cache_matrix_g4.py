"""H-1 Stage 3, gap G4: a school admin's cached view of a user outlives a
school move.

`UserCacheMixin.get_cache_key` scopes a `retrieve` read by the REQUESTING
user's own generation only (`SCOPE_USER, user_id`). When a teacher moves to
another school, `_viewer_scopes_for_signal` (users/signals.py) used to bump
`SCOPE_SCHOOL` for the old and new school -- correct for dashboards keyed on
the school -- but nothing bumped the viewing school admin's OWN `usr`
generation, so their cached `GET users/<teacher.pk>` kept serving a 200 for
a teacher who had since left their school and dropped out of their
queryset entirely (`get_queryset` filters school admins to
`Q(school_id=user.school_id)`).

FIXED (H-1 Stage 3): `viewer_scopes_for_users` (users/signals.py) now also
bumps `usr` for every SCHOOL_ADMIN of each affected school. This suite
proves BOTH directions of the move: the OLD school's admin loses their
cached access (200 -> 404, matching the real queryset) and the NEW
school's admin gains it immediately (404 -> 200) rather than being stuck
on a stale "not found".

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

from AutoGrader.tests_cache_matrix_support import FreshnessMatrixMixin, Read
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
        self.admin_b = make_active_user(
            "g4-admin-b@x.test",
            UserTypes.SCHOOL_ADMIN,
            "G4AdminB",
            school=self.school_b,
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
            Read(
                "old school admin's view of the teacher", self.admin, self.teacher_url
            ),
            Read(
                "new school admin's view of the teacher",
                self.admin_b,
                self.teacher_url,
            ),
        ]

    def move_teacher_to_school_b(self):
        client = APIClient()
        client.force_authenticate(self.superadmin)
        response = client.patch(
            self.teacher_url, {"school": str(self.school_b.pk)}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.content)

    def test_school_move_now_refreshes_both_admins_immediately(self):
        result = self.run_matrix(
            "move teacher to another school (G4 fixed)",
            self.reads(),
            self.move_teacher_to_school_b,
        )
        self.assert_no_stale(
            result,
            expect_changed=[
                "old school admin's view of the teacher",
                "new school admin's view of the teacher",
            ],
        )
        by_label = {o.label: o for o in result.outcomes}
        old = by_label["old school admin's view of the teacher"]
        self.assertEqual(old.cached_after[0], 404, result.table())
        self.assertEqual(old.truth_after[0], 404, result.table())
        new = by_label["new school admin's view of the teacher"]
        self.assertEqual(new.cached_after[0], 200, result.table())
        self.assertEqual(new.truth_after[0], 200, result.table())
