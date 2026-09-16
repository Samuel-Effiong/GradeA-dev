"""H-1 Stage 3, pre-existing staleness P4: repair management commands
invalidate nothing.

`strip_html_from_assignment_titles` (and its siblings
`repair_question_blooms_levels`, `strip_duplicate_option_letters`,
`backfill_assignment_rigor`) write with `bulk_update`, which fires no
signal at all -- not `post_save`, so not the legacy wildcard receiver
either. A teacher's cached assignment list keeps showing the un-repaired
title until the cache entry's TTL expires on its own.

This is pre-existing staleness the owner decided Stage 3 fixes too (plan
§ "Old staleness"): live under BOTH mechanisms today.

Fixtures follow the Stage 3 rule (plan §0): the assignment carries exactly
the malformed title the command is written to repair (production wrote
titles like this before the pre_save sanitizer existed); the mutation
under test runs the real management command.

Real Redis + real Postgres.
"""

from io import StringIO

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.test import TransactionTestCase
from django.urls import reverse

from assignments.models import Assignment, AssignmentStatus
from assignments.signals import sanitize_assignment_title
from AutoGrader.tests_cache_matrix_support import (
    STALE,
    FreshnessMatrixMixin,
    Read,
    legacy_wildcards_disabled,
)
from classrooms.models import Course, Session
from users.models import UserTypes

User = get_user_model()


def question(number=1):
    return {
        "question_number": number,
        "question_text": f"Q{number}",
        "question_type": "OBJECTIVE",
        "points": 10,
        "options": ["one", "two"],
        "rubric": [],
        "model_answer": "one",
    }


def make_active_user(email, user_type, first_name):
    return User.objects.create_user(
        email=email,
        password="password123",  # nosec  # pragma: allowlist secret
        user_type=user_type,
        is_active=True,
        first_name=first_name,
        last_name=user_type.title(),
    )


class TitleRepairCommandFreshnessTests(FreshnessMatrixMixin, TransactionTestCase):
    """P4: the title-repair command must refresh the owning teacher's list."""

    reset_sequences = True

    def setUp(self):
        cache.clear()
        self.patched_modules = self.enterContext(legacy_wildcards_disabled())
        self.assertTrue(self.patched_modules, "no legacy module was patched")

        self.teacher = make_active_user("p4-teacher@x.test", UserTypes.TEACHER, "P4T")
        self.session = Session.objects.create(name="P4 term", teacher=self.teacher)
        self.course = Course.objects.create(
            name="P4 course", teacher=self.teacher, session=self.session
        )
        self.assignment = Assignment.objects.create(
            title="<p>Matrices Exam</p>",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            questions=[question()],
        )
        # The pre_save sanitizer (assignments.signals.sanitize_assignment_title)
        # would normally strip this on save -- this fixture stands in for a
        # row written before that sanitizer existed, which is exactly what
        # the command exists to repair. Disconnect it for the one write,
        # not the whole receiver, so every other signal on this sender
        # keeps running normally.
        from django.db.models.signals import pre_save

        pre_save.disconnect(sanitize_assignment_title, sender=Assignment)
        try:
            self.assignment.title = "<p>Matrices Exam</p>"
            self.assignment.save(update_fields=["title"])
        finally:
            pre_save.connect(sanitize_assignment_title, sender=Assignment)
        self.assignment.refresh_from_db()
        self.assertIn("<p>", self.assignment.title)

        self.list_url = reverse("assignment-list")

    def reads(self):
        return [Read("teacher's assignment list", self.teacher, self.list_url)]

    def run_repair_command(self):
        call_command("strip_html_from_assignment_titles", stdout=StringIO())
        self.assignment.refresh_from_db()
        self.assertNotIn("<p>", self.assignment.title)

    def test_repair_command_currently_leaves_the_teachers_list_stale(self):
        result = self.run_matrix(
            "strip_html_from_assignment_titles (P4 gap, bulk_update fires no signal)",
            self.reads(),
            self.run_repair_command,
        )
        verdicts = {o.label: o.verdict for o in result.outcomes}
        self.assertEqual(verdicts, {"teacher's assignment list": STALE}, result.table())
