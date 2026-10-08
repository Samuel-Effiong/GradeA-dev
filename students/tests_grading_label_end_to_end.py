"""BE-I-04 slice C, the delta after verification: one test from a real
entry point, through the REAL grading service, to the row and the stored
audit entry, with only the provider call replaced. Handed over by the
Next-stage Checker (its probe PC0) and adopted here (Senior Manager's
ruling, 2026-10-07).

The other tests are in two halves that meet at one object: the grading
service's tests drive the real service and read the run; the route tests
replace the whole grading service with a stand-in that fills the run by
hand. This one joins them:

  * the immediate route, a short paper answered by a backup model: the six
    columns in the database, and the stored GRADING_COMPLETED entry;
  * then a second student with the identical answer: no provider call; the
    row names the model that first answered; the entry says no fresh call
    was made and gives no model as served.

Seen red in the Checker's run three ways (the save handed no run; a reused
answer counted as the main model's; the pipeline not handing its run to
the grading service), and in this slice's own mutant battery.
"""

import json
from unittest.mock import MagicMock, patch

from django.core.cache import cache as django_cache
from django.db import connection
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from ai_processor import services as ai_services
from ai_processor.services import AIProcessor
from assignments.tests_grading_audit_events import make_classroom, make_submission
from audit.enums import AuditAction
from audit.models import AuditEvent
from students.grading_label import LABEL_FIELDS, UNLABELLED
from students.models import StudentSubmission
from users.models import CustomUser, UserTypes

MAIN = ai_services.MAIN_MODEL
BACKUP = ai_services.GRADING_FALLBACK_MODELS[0]
TABLE = StudentSubmission._meta.db_table

QUESTION = {
    "question_number": 1,
    "question_text": "Why does ice float?",
    "question_type": "ESSAY",
    "points": 10,
    "options": [],
    "rubric": [
        {"level": "excellent", "description": "Great", "points": 10},
        {"level": "good", "description": "Good", "points": 8},
        {"level": "poor", "description": "Poor", "points": 0},
    ],
    "model_answer": "It is less dense than water.",
}
ANSWERS = [{"question_number": 1, "answer_html": "<p>Ice is less dense.</p>"}]
REPLY = {
    "question_evaluations": [
        {
            "question_number": 1,
            "score_awarded": 8,
            "max_points": 10,
            "evidence_quotes": ["Ice is less dense"],
        }
    ],
    "overall_performance_analysis": "Good.",
    "grading_confidence": 90,
    "recommendations": [],
}


def _reply(model):
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = json.dumps(REPLY)
    response.usage.total_tokens = 100
    response.model = model
    return response


def _row(submission):
    columns = ("score", *LABEL_FIELDS)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT %s FROM %s WHERE id = %%s" % (", ".join(columns), TABLE),
            [submission.pk],
        )
        return dict(zip(columns, cursor.fetchone(), strict=True))


@override_settings(GRADING_SECOND_OPINION_ENABLED=False)
class PC0FromTheRouteThroughTheRealServiceTest(APITestCase):
    def setUp(self):
        self.teacher, self.student, self.course, self.assignment = make_classroom("pc0")
        self.assignment.questions = [QUESTION]
        self.assignment.save(update_fields=["questions"])
        self.first = make_submission(self.assignment, self.student)
        other = CustomUser.objects.create_user(
            email="pc0-other-student@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
        )
        self.second = make_submission(self.assignment, other)
        StudentSubmission.objects.filter(pk__in=[self.first.pk, self.second.pk]).update(
            answers=ANSWERS
        )
        for target in (
            "students.services.student_summary_async",
            "students.services.launch_processing_task",
        ):
            patcher = patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)
        django_cache.clear()
        self.addCleanup(django_cache.clear)
        self.client.force_authenticate(user=self.teacher)

    def post_grade(self, submission):
        url = reverse("student-submission-grade", kwargs={"pk": submission.pk})
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(url)
        self.assertEqual(response.status_code, 200, response.content)

    def entry(self, submission):
        events = AuditEvent.objects.filter(
            action=AuditAction.GRADING_COMPLETED, target_id=submission.id
        )
        self.assertEqual(events.count(), 1)
        return events.get().metadata

    @patch.object(AIProcessor, "execute_graded_task")
    def test_a_backup_answers_then_a_second_student_reuses_it(self, mock_execute):
        mock_execute.return_value = _reply(BACKUP)

        self.post_grade(self.first)
        self.assertEqual(mock_execute.call_count, 1)
        row = _row(self.first)
        self.assertEqual(float(row["score"]), 8.0)
        self.assertNotIn(UNLABELLED, [row[name] for name in LABEL_FIELDS])
        self.assertEqual(row["grading_model"], BACKUP)
        self.assertEqual(row["grading_fallback_used"], "yes")
        self.assertRegex(row["grading_config_version"], r"\Acfg:[0-9a-f]{12}\Z")
        self.assertEqual(
            row["grading_prompt_version"],
            ai_services.GRADING_ASSIGNMENT_PROMPT.version,
        )
        entry = self.entry(self.first)
        self.assertEqual(entry["model"], BACKUP)
        self.assertEqual(entry["models_served"], [BACKUP])
        self.assertEqual(entry["models_reused"], [])
        self.assertEqual(entry["models_second_opinion"], [])
        self.assertEqual(entry["fresh_backup_used"], "yes")
        self.assertEqual(entry["grading_config_version"], row["grading_config_version"])
        self.assertEqual(entry["prompt_version"], row["grading_prompt_version"])
        self.assertEqual(entry["strictness"], row["grading_strictness"])

        # The second student: the identical answer, no provider call.
        mock_execute.reset_mock()
        mock_execute.return_value = _reply(MAIN)
        self.post_grade(self.second)
        self.assertEqual(mock_execute.call_count, 0)
        row = _row(self.second)
        self.assertEqual(float(row["score"]), 8.0)
        self.assertEqual(row["grading_model"], BACKUP)
        self.assertEqual(row["grading_fallback_used"], "yes")
        entry = self.entry(self.second)
        self.assertEqual(entry["model"], BACKUP)
        self.assertEqual(entry["models_served"], [])
        self.assertEqual(entry["models_reused"], [BACKUP])
        self.assertEqual(entry["fresh_backup_used"], "no_fresh_call")
