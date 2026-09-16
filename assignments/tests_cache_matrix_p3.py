"""H-1 Stage 3, pre-existing staleness P3: AssignmentGenerationMessage saves
have no receiver at all.

Unlike `AssignmentGenerationSession` (G9, wildcard-only), the message model
had NO signal receiver whatsoever -- not even the legacy wildcard. Adding a
message to a session left the owning teacher's cached retrieve of that
session (which nests every message, `AssignmentGenerationSessionDetailSerializer`)
showing the conversation as it was before the new message arrived.

FIXED (H-1 Stage 3): `clear_assignment_generation_message_cache`
(assignments/signals.py) now bumps `usr(session.user_id)` on message
save/delete, the same receiver G9 adds for the session itself. This suite
proves the owner's session retrieve refreshes and an unrelated teacher's
own retrieve (never had access, 404 both times) stays UNAFFECTED.

This is pre-existing staleness the owner decided Stage 3 fixes too (plan §
"Old staleness", tracked as P3, "covered by G9").

Fixtures follow the Stage 3 rule (plan §0): the session is created with
exactly the fields the real `generate` endpoint sets; the message under
test is created with exactly the fields that same endpoint sets
(`session`, `role`, `content`).

Real Redis + real Postgres.
"""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TransactionTestCase
from django.urls import reverse

from assignments.models import (
    AssignmentGenerationMessage,
    AssignmentGenerationRole,
    AssignmentGenerationSession,
)
from AutoGrader.tests_cache_matrix_support import (
    UNAFFECTED,
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


class GenerationMessageFreshnessTests(FreshnessMatrixMixin, TransactionTestCase):
    """P3: a new message must refresh the owner's cached session retrieve."""

    reset_sequences = True

    def setUp(self):
        cache.clear()
        self.patched_modules = self.enterContext(legacy_wildcards_disabled())
        self.assertTrue(self.patched_modules, "no legacy module was patched")

        self.teacher = make_active_user("p3-teacher@x.test", UserTypes.TEACHER, "P3T")
        self.other_teacher = make_active_user(
            "p3-other-teacher@x.test", UserTypes.TEACHER, "P3Other"
        )
        self.term = Session.objects.create(name="P3 term", teacher=self.teacher)
        self.course = Course.objects.create(
            name="P3 course", teacher=self.teacher, session=self.term
        )
        self.generation_session = AssignmentGenerationSession.objects.create(
            user=self.teacher, course=self.course, title="P3 prompt"
        )

        self.retrieve_url = reverse(
            "assignment-generation-session-detail", args=[self.generation_session.pk]
        )

    def reads(self):
        return [
            Read("teacher's session retrieve", self.teacher, self.retrieve_url),
            Read(
                "unrelated teacher's session retrieve",
                self.other_teacher,
                self.retrieve_url,
            ),
        ]

    def add_message(self):
        AssignmentGenerationMessage.objects.create(
            session=self.generation_session,
            role=AssignmentGenerationRole.USER,
            content="a follow-up prompt",
        )

    def test_new_message_now_refreshes_only_the_owner(self):
        result = self.run_matrix(
            "add a generation message (P3 fixed)",
            self.reads(),
            self.add_message,
        )
        self.assert_no_stale(result, expect_changed=["teacher's session retrieve"])
        verdicts = {o.label: o.verdict for o in result.outcomes}
        self.assertEqual(
            verdicts["unrelated teacher's session retrieve"],
            UNAFFECTED,
            result.table(),
        )
