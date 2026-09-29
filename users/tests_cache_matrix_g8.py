"""H-1 Stage 3, gap G8: a CustomUser change reaches no superadmin.

`clear_user_cache` (users/signals.py) used to bump only the changed user's
own `SCOPE_USER`, `SCOPE_ANY_USER` and `SCOPE_GLOBAL`, plus (for
`CustomUser` only) `viewer_scopes_for_users` -- school and teacher fan-out.
None of that was a superadmin's OWN `SCOPE_USER`, and `UserCacheMixin` keys
the superadmin-only `GET users` list by the requesting superadmin's
generation alone, so a superadmin's cached list of every user in the
system did not notice another user's profile edit.

FIXED (H-1 Stage 3): `clear_user_cache` now also bumps `usr` for every
superadmin (`superadmin_user_ids()`), for both `CustomUser` and `Settings`
changes. This suite proves EVERY superadmin's own list refreshes, not
just one.

Fixtures follow the Stage 3 rule (plan §0): rows are created directly, with
exactly the fields production sets; the mutation under test runs through
the real `PATCH users/<pk>` self-edit endpoint.

Real Redis + real Postgres.
"""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TransactionTestCase
from django.urls import reverse
from rest_framework.test import APIClient

from AutoGrader.tests_cache_matrix_support import FreshnessMatrixMixin, Read
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


class SuperAdminUserListFreshnessTests(FreshnessMatrixMixin, TransactionTestCase):
    """G8: a superadmin's cached user list must notice any user's edit."""

    reset_sequences = True

    def setUp(self):
        cache.clear()

        self.superadmin = make_active_user(
            "g8-super@x.test", UserTypes.SUPER_ADMIN, "G8Super", is_superuser=True
        )
        self.other_superadmin = make_active_user(
            "g8-other-super@x.test",
            UserTypes.SUPER_ADMIN,
            "G8OtherSuper",
            is_superuser=True,
        )
        self.teacher = make_active_user("g8-teacher@x.test", UserTypes.TEACHER, "G8Old")

        self.list_url = reverse("user-list")

    def reads(self):
        return [
            Read("first superadmin's user list", self.superadmin, self.list_url),
            Read("second superadmin's user list", self.other_superadmin, self.list_url),
        ]

    def rename_teacher(self):
        client = APIClient()
        client.force_authenticate(self.teacher)
        response = client.patch(
            reverse("user-detail", args=[self.teacher.pk]), {"first_name": "G8New"}
        )
        self.assertEqual(response.status_code, 200, response.content)

    def test_a_users_own_edit_now_refreshes_every_superadmin(self):
        result = self.run_matrix(
            "teacher renames themselves (G8 fixed)",
            self.reads(),
            self.rename_teacher,
        )
        self.assert_no_stale(
            result,
            expect_changed=[
                "first superadmin's user list",
                "second superadmin's user list",
            ],
        )
