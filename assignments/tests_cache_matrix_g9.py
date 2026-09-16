"""H-1 Stage 3, gap G9: AssignmentGenerationSession changes are wildcard-only.

`clear_assignment_generation_session_cache` (assignments/signals.py) calls
only `delete_cache_patterns(...)` -- no `bump_many` at all. With the legacy
wildcards disabled, deleting a generation session leaves the owning
teacher's own cached generation-session list stale.

Fixtures follow the Stage 3 rule (plan §0): the session is created directly
with exactly the fields the real `generate` endpoint sets (`user`, `course`,
`title`); the mutation under test runs through the real
`DELETE assignment-generation-sessions/<pk>` endpoint.

Real Redis + real Postgres.
"""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TransactionTestCase
from django.urls import reverse
from rest_framework.test import APIClient

from assignments.models import AssignmentGenerationSession
from AutoGrader.tests_cache_matrix_support import (
    STALE,
    FreshnessMatrixMixin,
    Read,
    legacy_wildcards_disabled,
)
from classrooms.models import Course, Session
from users.models import UserTypes

User = get_user_model()


def make_active_user(email, user_type, first_name):
    return User.objects.create_user(
        email=email,
        password="password123",  # nosec  # pragma: allowlist secret
        user_type=user_type,
        is_active=True,
        first_name=first_name,
        last_name=user_type.title(),
    )


class GenerationSessionDeleteFreshnessTests(FreshnessMatrixMixin, TransactionTestCase):
    """G9: deleting a generation session must refresh the owner's own list."""

    reset_sequences = True

    def setUp(self):
        cache.clear()
        self.patched_modules = self.enterContext(legacy_wildcards_disabled())
        self.assertTrue(self.patched_modules, "no legacy module was patched")

        self.teacher = make_active_user("g9-teacher@x.test", UserTypes.TEACHER, "G9T")
        self.term = Session.objects.create(name="G9 term", teacher=self.teacher)
        self.course = Course.objects.create(
            name="G9 course", teacher=self.teacher, session=self.term
        )
        self.generation_session = AssignmentGenerationSession.objects.create(
            user=self.teacher, course=self.course, title="G9 prompt"
        )

        self.list_url = reverse("assignment-generation-session-list")

    def reads(self):
        return [Read("teacher's generation-session list", self.teacher, self.list_url)]

    def delete_session(self):
        client = APIClient()
        client.force_authenticate(self.teacher)
        response = client.delete(
            reverse(
                "assignment-generation-session-detail",
                args=[self.generation_session.pk],
            )
        )
        self.assertEqual(response.status_code, 204, response.content)

    def test_session_delete_currently_leaves_the_owners_list_stale(self):
        result = self.run_matrix(
            "delete generation session (G9 gap, wildcard-only receiver)",
            self.reads(),
            self.delete_session,
        )
        verdicts = {o.label: o.verdict for o in result.outcomes}
        self.assertEqual(
            verdicts, {"teacher's generation-session list": STALE}, result.table()
        )
