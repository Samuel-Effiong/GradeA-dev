"""H-1 Stage 3, gap G6: school-owned Session edits refresh nobody.

`clear_session_cache` (classrooms/signals.py) used to bump only
`SCOPE_USER(teacher_id)` and `SCOPE_SCHOOL(school_id)`. A SCHOOL-owned
`Session` has `teacher=None` (only an INDIVIDUAL session sets it), so the
`SCOPE_USER` bump was a no-op -- and `UserCacheMixin` keys the sessions
list only by the REQUESTING user's own generation, so the `SCOPE_SCHOOL`
bump reached nobody's cached list either. Not even the acting school
admin's own cached list refreshed.

FIXED (H-1 Stage 3): the receiver now also bumps `usr` for the school's
SCHOOL_ADMIN and TEACHER users, `created_by`, and every superadmin. This
suite proves the admin's own list refreshes and an unrelated admin at a
DIFFERENT school stays UNAFFECTED.

Fixtures follow the Stage 3 rule (plan §0): rows are created directly, with
exactly the fields production sets; the mutation under test runs through
the real `PATCH sessions/<pk>` endpoint.

Real Redis + real Postgres.
"""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TransactionTestCase
from django.urls import reverse
from rest_framework.test import APIClient

from AutoGrader.tests_cache_matrix_support import UNAFFECTED, FreshnessMatrixMixin, Read
from classrooms.models import School, Session, SessionOwnerType
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


class SchoolSessionEditFreshnessTests(FreshnessMatrixMixin, TransactionTestCase):
    """G6: editing a school-owned session must refresh the admin's own list."""

    reset_sequences = True

    def setUp(self):
        cache.clear()

        self.school = School.objects.create(name="G6 School")
        self.other_school = School.objects.create(name="G6 Other School")
        self.admin = make_active_user(
            "g6-admin@x.test", UserTypes.SCHOOL_ADMIN, "G6Admin", school=self.school
        )
        self.other_school_admin = make_active_user(
            "g6-other-admin@x.test",
            UserTypes.SCHOOL_ADMIN,
            "G6OtherAdmin",
            school=self.other_school,
        )
        self.session = Session.objects.create(
            name="G6 term",
            owner_type=SessionOwnerType.SCHOOL,
            school=self.school,
            teacher=None,
        )

        self.list_url = reverse("session-list")

    def reads(self):
        return [
            Read("school admin's session list", self.admin, self.list_url),
            Read(
                "other school's admin session list",
                self.other_school_admin,
                self.list_url,
            ),
        ]

    def rename_session(self):
        client = APIClient()
        client.force_authenticate(self.admin)
        response = client.patch(
            reverse("session-detail", args=[self.session.pk]),
            {"name": "G6 term renamed"},
        )
        self.assertEqual(response.status_code, 200, response.content)

    def test_session_rename_now_refreshes_the_admins_own_list(self):
        result = self.run_matrix(
            "rename school session (G6 fixed)",
            self.reads(),
            self.rename_session,
        )
        self.assert_no_stale(result, expect_changed=["school admin's session list"])
        verdicts = {o.label: o.verdict for o in result.outcomes}
        self.assertEqual(
            verdicts["other school's admin session list"], UNAFFECTED, result.table()
        )
