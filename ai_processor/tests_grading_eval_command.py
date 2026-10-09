"""
Coverage for the grading_eval management command — the accuracy
scoreboard over persisted grading data.

Fixtures are crafted feedback/resolution blobs (not live pipeline runs):
the command's job is to read what production wrote, so the tests write
exactly the persisted shapes and assert the computed numbers — coverage
split, trigger mix, disagreement rates and segments, teacher-alignment
attribution (confirmed / overridden-closer-to-whom), QA-sample vs
triggered calibration, regrade baseline, and the malformed-feedback and
empty-window guard rails.

Run with:
    python manage.py test ai_processor.tests_grading_eval_command
"""

import json
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from assignments.models import Assignment
from classrooms.models import Course, Session
from students.models import StudentSubmission
from users.models import CustomUser, UserTypes

A_MODEL = "primary-model"
B_MODEL = "second-model"


def _run_command(*args):
    out = StringIO()
    call_command("grading_eval", *args, stdout=out)
    return out.getvalue()


def _run_json(*args):
    return json.loads(_run_command("--json", *args))


class GradingEvalCommandTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        stamp = timezone.now().timestamp()
        cls.teacher = CustomUser.objects.create_user(
            email=f"eval-teacher-{stamp}@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        session = Session.objects.create(name="S", teacher=cls.teacher)
        course = Course.objects.create(
            name="Eval Course", teacher=cls.teacher, session=session
        )
        cls.assignment = Assignment.objects.create(
            title="Eval assignment",
            course=course,
            questions=[
                {
                    "question_number": 1,
                    "question_text": "Q1",
                    "question_type": "ESSAY",
                    "points": 20,
                    "options": [],
                    "rubric": [],
                    "model_answer": "m",
                },
                {
                    "question_number": 2,
                    "question_text": "Q2",
                    "question_type": "SHORT-ANSWER",
                    "points": 10,
                    "options": [],
                    "rubric": [],
                    "model_answer": "m",
                },
            ],
        )
        cls._make_fixture_submissions()

    @classmethod
    def _submission(cls, tag, **fields):
        student = CustomUser.objects.create_user(
            email=f"eval-{tag}-{timezone.now().timestamp()}@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
        )
        submission = StudentSubmission.objects.create(
            assignment=cls.assignment,
            student=student,
            answers=[{"question_number": 1, "answer_html": "x"}],
        )
        fields.setdefault("graded_at", timezone.now())
        StudentSubmission.objects.filter(pk=submission.pk).update(**fields)
        return submission

    @classmethod
    def _make_fixture_submissions(cls):
        def evaluations(score_q1=16, score_q2=8):
            return [
                {"question_number": 1, "score_awarded": score_q1},
                {"question_number": 2, "score_awarded": score_q2},
            ]

        # S1 — pure QA sample, full agreement, high confidence.
        cls._submission(
            "s1",
            grading_confidence=90,
            feedback={
                "grading_model": A_MODEL,
                "question_evaluations": evaluations(),
                "second_opinion": {
                    "model": B_MODEL,
                    "selected": {"1": ["qa_sample"], "2": ["qa_sample"]},
                    "agreements": [1, 2],
                    "disagreements": [],
                },
            },
        )

        # S2 — high-stakes trigger, one critical disagreement on Q1,
        # teacher CONFIRMED grader A afterwards.
        cls._submission(
            "s2",
            grading_confidence=90,
            feedback={
                "grading_model": A_MODEL,
                "question_evaluations": evaluations(),
                "second_opinion": {
                    "model": B_MODEL,
                    "selected": {"1": ["high_stakes"]},
                    "agreements": [],
                    "disagreements": [
                        {
                            "question_number": 1,
                            "a": {"score_awarded": 16},
                            "b": {"score_awarded": 0},
                            "severity": {
                                "gap_points": 16,
                                "gap_fraction": 0.8,
                                "levels_apart": None,
                                "tier": "critical",
                            },
                        }
                    ],
                },
            },
            needs_review=False,
            review_reasons=[
                {"type": "grader_disagreement", "question_number": 1},
                {"resolved": "confirmed", "by": "t", "at": "2026-08-08"},
            ],
        )

        # S3 — low-confidence trigger, disagreement on Q1, teacher
        # OVERRODE to a score near grader B's implied total, and the
        # submission was regraded. A total (ai_score) 15; gap b-a = -15
        # so B-implied total 0; teacher's final score 2 → B closer.
        cls._submission(
            "s3",
            grading_confidence=40,
            ai_score=15,
            score=2,
            max_points=20,
            was_regraded=True,
            feedback={
                "grading_model": A_MODEL,
                "question_evaluations": evaluations(score_q1=15),
                "second_opinion": {
                    "model": B_MODEL,
                    "selected": {"1": ["low_confidence"], "2": ["low_confidence"]},
                    "agreements": [2],
                    "disagreements": [
                        {
                            "question_number": 1,
                            "a": {"score_awarded": 15},
                            "b": {"score_awarded": 0},
                            "severity": {
                                "gap_points": 15,
                                "gap_fraction": 0.75,
                                "levels_apart": None,
                                "tier": "critical",
                            },
                        }
                    ],
                },
            },
            needs_review=False,
            review_reasons=[
                {"type": "grader_disagreement", "question_number": 1},
                {"resolved": "overridden", "by": "t", "at": "2026-08-08"},
            ],
        )

        # S4 — malformed feedback blob (ledger #10).
        cls._submission("s4", feedback="not a dict at all")

        # S5 — second opinion errored out (non-fatal path).
        cls._submission(
            "s5",
            grading_confidence=90,
            feedback={
                "grading_model": A_MODEL,
                "question_evaluations": evaluations(),
                "second_opinion": {"error": "second model exploded"},
            },
        )

    def test_coverage_and_unparseable(self):
        report = _run_json("--days", "365")
        self.assertEqual(report["graded_submissions"], 5)
        self.assertEqual(report["unparseable_feedback"], 1)
        self.assertEqual(report["second_opinion_coverage"]["ran"], 3)
        self.assertEqual(report["second_opinion_coverage"]["error"], 1)
        self.assertEqual(report["second_opinion_coverage"]["not_run"], 0)

    def test_trigger_mix(self):
        report = _run_json("--days", "365")
        self.assertEqual(report["trigger_mix"]["qa_sample"], 2)
        self.assertEqual(report["trigger_mix"]["high_stakes"], 1)
        self.assertEqual(report["trigger_mix"]["low_confidence"], 2)

    def test_disagreement_rates_and_segments(self):
        report = _run_json("--days", "365")
        disagreement = report["disagreement"]
        # Compared: S1 2 + S2 1 + S3 2 = 5; disagreed: S2 1 + S3 1 = 2.
        self.assertEqual(disagreement["questions_compared"], 5)
        self.assertEqual(disagreement["questions_disagreed"], 2)
        self.assertEqual(disagreement["question_rate"], 0.4)
        self.assertEqual(disagreement["submissions_flagged"], 2)
        self.assertEqual(disagreement["by_tier"]["critical"], 2)
        # Q1 is ESSAY (disagreed twice of three comparisons), Q2 is
        # SHORT-ANSWER (never disagreed).
        essay = disagreement["by_question_type"]["ESSAY"]
        self.assertEqual((essay["disagreed"], essay["compared"]), (2, 3))
        short = disagreement["by_question_type"]["SHORT-ANSWER"]
        self.assertEqual((short["disagreed"], short["compared"]), (0, 2))
        pair = disagreement["by_model_pair"][f"{A_MODEL} × {B_MODEL}"]
        self.assertEqual(pair["compared"], 5)

    def test_teacher_alignment(self):
        # Ledger #12.
        report = _run_json("--days", "365")
        alignment = report["teacher_alignment"]
        self.assertEqual(alignment["confirmed_a_vindicated"], 1)
        self.assertEqual(alignment["overridden"], 1)
        self.assertEqual(alignment["overridden_b_closer"], 1)
        self.assertEqual(alignment["overridden_a_closer"], 0)

    def test_calibration_split(self):
        # Ledger #13: the pure-sample run (S1) is measured separately
        # from the triggered runs (S2, S3).
        report = _run_json("--days", "365")
        calibration = report["calibration"]
        self.assertEqual(calibration["qa_sample_rate"], 0.0)  # 0/2
        self.assertEqual(calibration["triggered_rate"], round(2 / 3, 4))
        low_band = calibration["by_confidence_band"]["<60"]
        self.assertEqual((low_band["disagreed"], low_band["compared"]), (1, 2))

    def test_regrade_baseline(self):
        report = _run_json("--days", "365")
        regrade = report["regrade_baseline"]
        self.assertEqual(regrade["total"], 5)
        self.assertEqual(regrade["regraded"], 1)
        # |2 - 15| / 20 = 0.65
        self.assertEqual(regrade["mean_abs_delta_fraction"], 0.65)

    def test_text_output_renders_sections(self):
        output = _run_command("--days", "365")
        for marker in [
            "1. Second-opinion coverage",
            "2. Disagreement",
            "3. Teacher alignment",
            "4. Trigger calibration",
            "5. Regrade baseline",
        ]:
            self.assertIn(marker, output)

    def test_empty_window_is_clean(self):
        # Ledger #11: --days 0 → nothing graded "since now".
        output = _run_command("--days", "0")
        self.assertIn("No graded submissions", output)
        report = _run_json("--days", "0")
        self.assertEqual(report["graded_submissions"], 0)
