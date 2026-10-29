"""H-260: a failed assignment save quoted the error's own text in a 400.

Found by H-255's survey: `AssignmentSerializer.create`
(`assignments/serializers.py:231`) caught any exception from the save and
raised "Failed to create assignment and rubric: {e}". A database error's text
names tables, constraints and the values in the row, and the 400 carried it to
the client. The route that reaches it is the generated-draft save
(`POST assignments/generated-drafts/<message_id>/save`); the assignment create
route uses its own serializer and never calls this one's `create`. The upload
pipeline also saves through it, but there the error is stored as the tracked
row's sentence and logged by class (H-208, H-219), so no client reads it.

The rule (the Senior Manager's ruling, 2026-10-27): the client gets one fixed
sentence, and the server log gets the error's class at WARNING, with no text
and no traceback.
"""

from unittest.mock import patch

from django.db import IntegrityError
from django.urls import reverse
from rest_framework import serializers, status

from assignments.models import Assignment, AssignmentGenerationMessage
from assignments.tests import generated_assignment_payload
from assignments.tests_ai_output_allowlist import GENERATE, AIOutputAllowListFixture

#: What the database might say. Each fragment is one the client must not read.
PROBE = (
    'duplicate key value violates unique constraint "h260_probe_constraint" '
    "DETAIL: Key (title)=(h260-probe-title) already exists."
)
FRAGMENTS = ("duplicate key", "h260_probe_constraint", "h260-probe-title")
#: The fixed sentence the client reads instead.
SENTENCE = (
    "We couldn't save this assignment. Please try again, and contact support "
    "if the problem continues."
)


def failing_save():
    return patch.object(
        serializers.ModelSerializer, "create", side_effect=IntegrityError(PROBE)
    )


@patch(GENERATE)
class AFailedDraftSaveQuotesNoErrorTextTest(AIOutputAllowListFixture):
    def generate(self):
        response = self.client.post(
            reverse("assignment-generate", kwargs={"course_id": self.course_b.id}),
            {"prompt": "Create a one-question biology quiz."},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        return AssignmentGenerationMessage.objects.get(id=response.data["message_id"])

    def save_draft(self, message):
        return self.client.post(
            reverse(
                "assignment-save-generated-draft", kwargs={"message_id": message.id}
            ),
            {},
            format="json",
        )

    def save_draft_failing(self, mock_generate):
        mock_generate.return_value = generated_assignment_payload()
        message = self.generate()
        with failing_save():
            response = self.save_draft(message)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        return message, response

    def test_the_400_carries_none_of_the_errors_text(self, mock_generate):
        _, response = self.save_draft_failing(mock_generate)

        body = response.content.decode()
        for fragment in FRAGMENTS:
            self.assertNotIn(fragment, body)

    def test_the_400_reads_the_fixed_sentence(self, mock_generate):
        _, response = self.save_draft_failing(mock_generate)

        self.assertIn(SENTENCE, response.content.decode())

    def test_the_server_log_names_the_class_at_warning(self, mock_generate):
        with self.assertLogs("assignments.serializers", level="WARNING") as logs:
            self.save_draft_failing(mock_generate)

        self.assertEqual(len(logs.records), 1)
        [record] = logs.records
        self.assertEqual(record.levelname, "WARNING")
        self.assertIn("IntegrityError", record.getMessage())
        for fragment in FRAGMENTS:
            self.assertNotIn(fragment, record.getMessage())
        self.assertIsNone(record.exc_info)

    def test_control_the_draft_stays_unsaved_and_saves_after(self, mock_generate):
        """Green before and after: nothing is saved by the failed attempt, and
        the same draft saves on the next one."""
        before = Assignment.objects.count()
        message, _ = self.save_draft_failing(mock_generate)

        message.refresh_from_db()
        self.assertIsNone(message.assignment_id)
        self.assertEqual(Assignment.objects.count(), before)

        response = self.save_draft(message)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        message.refresh_from_db()
        self.assertEqual(str(message.assignment_id), response.data["id"])
