"""
AUDIT-NULL-SCHOOL (a): a School Admin whose account has NO school is shown an
EMPTY activity log, not every event whose school is null.

WHAT WAS WRONG (found by reading, 2026-10-07)
---------------------------------------------
`SchoolAdminAuditEventListView.get_queryset` filtered `school_id =
request.user.school_id`. A user's school is nullable, and in Django a filter
of `school_id=None` means `IS NULL`: an admin with no school was shown every
audit event whose school is null: individual teachers' events, system events,
anonymous ones, failed sign-ins with the address and the IP.

How such an admin can exist (read): a super admin's PATCH of `user_type` to
SCHOOL_ADMIN or of `school` to null, the Django admin, a hard delete of a
School (the column is SET_NULL). Not by self-service.

THE RULE (approved 2026-10-08)
------------------------------
`IsSchoolAdmin` only checks the role, so the view itself answers an empty
list when the admin has no school (not an error: nothing is leaked and the
screen still renders). The rule "a SCHOOL_ADMIN must have a school" in the
user serializer is a separate beta row.

Run with:
    python manage.py test audit.tests_school_admin_without_a_school
"""

import uuid

from django.contrib.auth import get_user_model

from audit import history
from audit.enums import ActorRole, AuditOutcome
from audit.tests_query_api import (
    SCHOOL_ADMIN_URL,
    SUPER_ADMIN_URL,
    _make_event,
    _TwoSchools,
    rows_of,
)

CustomUser = get_user_model()


class SchoolAdminWithoutASchoolTests(_TwoSchools):
    def setUp(self):
        super().setUp()
        with history.suppressed():
            self.admin_none = CustomUser.objects.create_user(
                email="admin.nowhere@example.com",
                password="testpass123",  # pragma: allowlist secret
                user_type="SCHOOL_ADMIN",
                school=None,
                is_active=True,
            )
        # Events that belong to no school: a failed sign-in by an anonymous
        # caller (with an address in it), an individual teacher's event and a
        # system event.
        self.null_events = [
            _make_event(
                school_id=None,
                action="AUTH_LOGIN",
                actor_role=ActorRole.ANONYMOUS,
                actor_email="someone.private@elsewhere.example",
                outcome=AuditOutcome.FAILURE,
            ),
            _make_event(school_id=None, actor_email="individual.teacher@example.com"),
            _make_event(school_id=None, actor_role=ActorRole.SYSTEM, actor_email=""),
        ]

    def as_admin_none(self):
        self.client.force_authenticate(user=self.admin_none)

    def test_an_admin_with_no_school_is_shown_an_empty_list(self):
        self.as_admin_none()

        response = self.client.get(SCHOOL_ADMIN_URL)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(rows_of(response), [])
        self.assertNotIn("someone.private@elsewhere.example", response.content.decode())

    def test_a_school_id_parameter_does_not_widen_it(self):
        self.as_admin_none()

        response = self.client.get(SCHOOL_ADMIN_URL, {"school_id": str(uuid.uuid4())})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(rows_of(response), [])

    def test_an_admin_with_a_school_still_sees_only_that_school(self):
        """Green on the old code too: the null-school events are not in a
        school admin's view, and their own school's are."""
        self.as_admin_a()

        response = self.client.get(SCHOOL_ADMIN_URL)

        ids = {row["id"] for row in rows_of(response)}
        self.assertEqual(ids, {str(self.event_a.id)})
        for event in self.null_events:
            self.assertNotIn(str(event.id), ids)

    def test_the_super_admin_still_sees_the_null_school_events(self):
        """Green on the old code too: platform staff see everything."""
        self.as_superadmin()

        response = self.client.get(SUPER_ADMIN_URL)

        ids = {row["id"] for row in rows_of(response)}
        for event in self.null_events:
            self.assertIn(str(event.id), ids)
