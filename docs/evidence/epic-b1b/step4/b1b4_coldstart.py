"""B1b-4 cold-start run (NOTES §4.9): `backtest_credit_estimates --cold-start`
on the test database, seeded, with the provider's estimate mocked. No
production access. Run with PYTHONPATH=docs/evidence/epic-b1b/step4."""

from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from ai_processor.models import AICallRecord
from ai_processor.services import AIProcessor
from billing.testing.holds import ai_ready_teacher
from billing.tests.testing_estimates import grading_session, make_assignment, papers


class ColdStartRun(TestCase):
    def test_the_cold_start_command_runs(self):
        teacher = ai_ready_teacher("b1b4.coldstart@example.com", 10_000)
        assignment = make_assignment(teacher, "Cold start")
        papers(teacher, assignment, [100] * 10, days_ago=30)
        session = grading_session(teacher, assignment)
        batch = papers(teacher, assignment, [300, 300], batch=session, days_ago=5)
        AICallRecord.objects.filter(
            processing_task_id__in=[task.pk for task in batch]
        ).update(estimate=150)
        out = StringIO()
        with patch.object(AIProcessor, "estimate_messages_cost", return_value=100):
            call_command("backtest_credit_estimates", "--cold-start", stdout=out)
        text = out.getvalue()
        print("B1B4-COLDSTART\n" + text)
        self.assertIn("batches=11 coverage=0.9091", text)
        self.assertIn("suggested_factor=1.0 provider_gap_ratio=2.0", text)
