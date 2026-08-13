"""
Tests for the ground-truth grading benchmark (ai_processor/benchmark/).

Three layers, deliberately separated by what they cost:

1. Dataset integrity — pure data checks. No DB, no network, no model.
   The load-bearing one is that every expected score is a score the
   grader can actually produce: _finalize_grading_result snaps to rubric
   levels, so an off-level expectation would fail forever and the
   failure would look like a model problem rather than an authoring
   mistake.

2. Scoring units — the metric maths, against hand-built inputs.

3. Replay end-to-end — the whole benchmark through the real pipeline
   with recorded model responses. Free and deterministic; this is what
   the nightly Celery task runs.

Run with:
    python manage.py test ai_processor.tests_grading_benchmark
"""

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from unittest import skipUnless

from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from ai_processor.benchmark import runner, scoring
from ai_processor.benchmark import submissions as submissions_module
from ai_processor.benchmark.dataset import (
    ASSIGNMENTS,
    IDENTICAL_ANSWER_PROBES,
    OBJECTIVE,
    PARAPHRASE_PROBES,
    STUDENTS,
    AnswerSpec,
    allowed_scores,
    iter_dataset_errors,
    iter_expectation_errors,
)
from ai_processor.objective_grading import (
    CLAIMED_OUTCOMES,
    CORRECT,
    INCORRECT,
    NOT_ATTEMPTED,
    match_objective_answer,
)

RECORDINGS = runner.RECORDINGS_DIR / "responses.json.gz"
HAS_RECORDINGS = RECORDINGS.exists()


# ── 1. Dataset integrity ──────────────────────────────────────────────────


class DatasetIntegrityTest(SimpleTestCase):
    """No DB, no API key needed — pure data validation."""

    def test_assignment_structure_is_valid(self):
        errors = list(iter_dataset_errors())
        self.assertEqual(errors, [], "\n".join(errors))

    def test_every_expected_score_is_reachable(self):
        # THE critical check. Scores are snapped to the nearest rubric
        # level, so an expectation that is not itself a level value can
        # never be met no matter how correctly the model grades.
        errors = list(iter_expectation_errors())
        self.assertEqual(errors, [], "\n".join(errors))

    def test_every_student_answers_every_question(self):
        for assignment in ASSIGNMENTS:
            numbers = {q["question_number"] for q in assignment.questions}
            for student in STUDENTS:
                specs = submissions_module.answers_for(assignment.key, student.key)
                with self.subTest(assignment=assignment.key, student=student.key):
                    self.assertEqual(
                        {s.question_number for s in specs},
                        numbers,
                    )

    def test_identical_answer_probes_are_byte_identical(self):
        # If a stray edit desynchronised these, the consistency probe
        # would silently become vacuous rather than failing.
        for assignment_key, number in IDENTICAL_ANSWER_PROBES:
            strong = [
                s
                for s in submissions_module.answers_for(assignment_key, "strong")
                if s.question_number == number
            ][0]
            twin = [
                s
                for s in submissions_module.answers_for(assignment_key, "twin")
                if s.question_number == number
            ][0]
            with self.subTest(assignment=assignment_key, question=number):
                self.assertEqual(strong.answer_html, twin.answer_html)
                self.assertEqual(strong.expected_points, twin.expected_points)

    def test_paraphrase_probe_shares_little_vocabulary_with_model_answer(self):
        # The point of this probe is to separate "understands the
        # content" from "matched the keywords". If the answer drifted
        # towards the model answer's wording it would stop testing that.
        import re

        def words(text):
            return set(re.findall(r"[a-z]{5,}", re.sub(r"<[^>]+>", " ", text.lower())))

        for assignment_key, student_key, number in PARAPHRASE_PROBES:
            assignment = next(a for a in ASSIGNMENTS if a.key == assignment_key)
            question = assignment.question(number)
            spec = [
                s
                for s in submissions_module.answers_for(assignment_key, student_key)
                if s.question_number == number
            ][0]
            answer_words = words(spec.answer_html)
            model_words = words(question["model_answer"])
            overlap = answer_words & model_words
            ratio = len(overlap) / max(len(answer_words), 1)
            with self.subTest(assignment=assignment_key, question=number):
                self.assertLess(
                    ratio,
                    0.5,
                    f"paraphrase probe shares {ratio:.0%} of its vocabulary with "
                    f"model_answer ({sorted(overlap)}) — it no longer tests "
                    "understanding over keyword matching",
                )

    def test_both_grading_paths_are_exercised(self):
        # Tier 0 removes objective questions BEFORE the split, so the
        # count that decides single-pass vs batched is the LLM-bound one.
        # This caught a real gap: with 8 questions and 3 objectives the
        # maths paper landed on exactly 5 and every assignment took the
        # single-pass path, leaving batching untested.
        from ai_processor.services import GRADING_QUESTIONS_PER_CHUNK

        paths = set()
        for assignment in ASSIGNMENTS:
            llm_bound = sum(
                1 for q in assignment.questions if q["question_type"] != OBJECTIVE
            )
            paths.add(
                "batched" if llm_bound > GRADING_QUESTIONS_PER_CHUNK else "single"
            )
        self.assertEqual(
            paths,
            {"batched", "single"},
            "benchmark must cover both the single-pass and batched paths",
        )

    def test_objective_answers_match_the_real_tier0_matcher(self):
        """
        Run every objective answer through the actual deterministic
        matcher. A claimed outcome must agree with the ground truth; a
        deferred one is allowed (tier 0 never guesses) but the expected
        score still has to be 0 or full.
        """
        for assignment in ASSIGNMENTS:
            for student in STUDENTS:
                for spec in submissions_module.answers_for(assignment.key, student.key):
                    question = assignment.question(spec.question_number)
                    if question["question_type"] != OBJECTIVE:
                        continue
                    outcome = match_objective_answer(question, spec.answer_html)
                    with self.subTest(
                        assignment=assignment.key,
                        student=student.key,
                        question=spec.question_number,
                    ):
                        if outcome not in CLAIMED_OUTCOMES:
                            # Deferred to the LLM — fine, but the answer
                            # is still either right or wrong.
                            self.assertIn(
                                spec.expected_points,
                                (0, question["points"]),
                            )
                            continue
                        claimed = question["points"] if outcome == CORRECT else 0
                        self.assertEqual(
                            claimed,
                            spec.expected_points,
                            f"tier 0 said {outcome} (score {claimed}) but the "
                            f"dataset expects {spec.expected_points}",
                        )
                        if outcome == NOT_ATTEMPTED:
                            self.assertFalse(spec.answer_html.strip())
                        if outcome == INCORRECT:
                            self.assertTrue(spec.answer_html.strip())


# ── 2. Scoring units ──────────────────────────────────────────────────────


def _question(points=10, levels=(10, 7, 4, 0), qtype="SHORT-ANSWER"):
    return {
        "question_number": 1,
        "question_type": qtype,
        "points": points,
        "options": [],
        "rubric": [{"level": str(p), "points": p, "description": ""} for p in levels],
        "model_answer": "",
    }


@dataclass(frozen=True)
class _Assignment:
    key: str
    subject: str
    questions: list

    def question(self, number):
        return self.questions[0]


class ScoringTest(SimpleTestCase):
    def test_allowed_scores_includes_zero_for_a_non_zero_floor_rubric(self):
        question = _question(points=20, levels=(20, 15, 8, 2))
        self.assertEqual(allowed_scores(question), {0.0, 2.0, 8.0, 15.0, 20.0})

    def test_objective_allowed_scores_are_all_or_nothing(self):
        question = _question(points=3, levels=(), qtype=OBJECTIVE)
        question["rubric"] = []
        self.assertEqual(allowed_scores(question), {0.0, 3.0})

    def test_exact_match_is_exact(self):
        question = _question()
        spec = AnswerSpec(1, "x", 7, note="n")
        self.assertEqual(scoring.grade_one(question, spec, 7)["verdict"], "exact")

    def test_one_level_away_is_adjacent_when_not_exact_required(self):
        question = _question()  # ladder 10,7,4,0
        spec = AnswerSpec(1, "x", 7, note="n")
        result = scoring.grade_one(question, spec, 10)
        self.assertEqual(result["verdict"], "adjacent")
        # Awarded a HIGHER grade than deserved => lenient => positive.
        self.assertEqual(result["level_error"], 1)

    def test_one_level_away_is_a_failure_when_exact_is_required(self):
        question = _question()
        spec = AnswerSpec(1, "x", 7, exact=True, note="n")
        self.assertEqual(scoring.grade_one(question, spec, 10)["verdict"], "off")

    def test_two_levels_away_is_always_off(self):
        question = _question()
        spec = AnswerSpec(1, "x", 7, note="n")
        result = scoring.grade_one(question, spec, 0)
        self.assertEqual(result["verdict"], "off")
        # Harsher than deserved => negative.
        self.assertEqual(result["level_error"], -2)

    def test_off_ladder_score_is_reported_as_unreachable(self):
        # Only possible if rubric snapping regressed; it must surface as
        # a distinct verdict rather than crashing or silently rounding.
        question = _question()
        spec = AnswerSpec(1, "x", 7, note="n")
        self.assertEqual(
            scoring.grade_one(question, spec, 6.5)["verdict"], "unreachable"
        )

    def test_missing_evaluation_counts_as_a_failure(self):
        question = _question()
        spec = AnswerSpec(1, "x", 7, note="n")
        self.assertEqual(scoring.grade_one(question, spec, None)["verdict"], "off")

    def test_spearman_detects_perfect_and_inverted_ordering(self):
        self.assertEqual(scoring._spearman([1, 2, 3, 4], [10, 20, 30, 40]), 1.0)
        self.assertEqual(scoring._spearman([1, 2, 3, 4], [40, 30, 20, 10]), -1.0)

    def test_consistency_probe_skips_when_a_student_is_absent(self):
        # A filtered run must not report a false inconsistency.
        run = {"results": []}
        report = scoring.check_consistency(run, [("maths", 6)])
        self.assertEqual(report[0]["status"], "skipped")
        self.assertIsNone(report[0]["consistent"])

    def test_consistency_probe_flags_differing_scores(self):
        def result(student, score):
            return {
                "assignment_key": "maths",
                "student_key": student,
                "error": None,
                "grading": {
                    "question_evaluations": [
                        {"question_number": 6, "score_awarded": score}
                    ]
                },
            }

        run = {"results": [result("strong", 15), result("twin", 8)]}
        report = scoring.check_consistency(run, [("maths", 6)])
        self.assertEqual(report[0]["status"], "INCONSISTENT")
        self.assertFalse(report[0]["consistent"])

        run = {"results": [result("strong", 15), result("twin", 15)]}
        report = scoring.check_consistency(run, [("maths", 6)])
        self.assertTrue(report[0]["consistent"])

    def test_score_run_aggregates_bias_with_the_expected_sign(self):
        question = _question()
        assignment = _Assignment("t", "Test", [question])
        specs = [AnswerSpec(1, "x", 7, note="n")]
        run = {
            "mode": "replay",
            "results": [
                {
                    "assignment_key": "t",
                    "student_key": "s",
                    "assignment": assignment,
                    "specs": specs,
                    "grading": {
                        "question_evaluations": [
                            {"question_number": 1, "score_awarded": 10}
                        ]
                    },
                    "elapsed_seconds": 1,
                    "tokens": 10,
                    "error": None,
                }
            ],
        }
        report = scoring.score_run(run)
        self.assertEqual(report["overall"]["questions"], 1)
        self.assertEqual(report["overall"]["exact_rate"], 0.0)
        self.assertEqual(report["overall"]["within_one_level_rate"], 1.0)
        self.assertEqual(report["overall"]["mean_level_error"], 1.0)


# ── 3. Replay end-to-end ──────────────────────────────────────────────────


class BenchmarkFixtureMixin:
    def _make_teacher(self):
        from billing.models import (
            BillingInterval,
            CreditBucket,
            CreditBucketType,
            CreditWallet,
            PlanCategory,
            PlanTier,
            SubscriptionPlan,
            UserSubscription,
        )
        from users.models import CustomUser, UserTypes

        teacher = CustomUser.objects.create_user(
            email=f"benchmark-{timezone.now().timestamp()}@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            is_active=True,
        )
        plan = SubscriptionPlan.objects.create(
            name=f"bench-{teacher.id}",
            category=PlanCategory.INDIVIDUAL,
            tier=PlanTier.PRO,
            interval=BillingInterval.MONTHLY,
            monthly_credits=5_000_000,
            carry_over_percent=0,
            is_active=True,
        )
        now = timezone.now()
        UserSubscription.objects.create(
            user=teacher,
            plan=plan,
            is_active=True,
            billing_cycle_start=now,
            billing_cycle_end=now + timedelta(days=30),
            is_trial=False,
            auto_renew=False,
        )
        wallet, _ = CreditWallet.objects.get_or_create(user=teacher)
        # estimate_total_token adds a flat +20,000 baseline per call.
        CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=5_000_000,
            used_credits=0,
            expires_at=now + timedelta(days=30),
        )
        return teacher


class ReplayGuardTest(BenchmarkFixtureMixin, TestCase):
    def test_replay_without_recordings_raises_rather_than_calling_out(self):
        # A replay that quietly fell through to a live call would spend
        # real money on what is advertised as the free path.
        teacher = self._make_teacher()
        with self.assertRaises(runner.MissingRecordingError):
            runner.execute_benchmark(
                teacher,
                mode=runner.MODE_REPLAY,
                recordings_dir=Path("/nonexistent/benchmark/recordings"),
            )


@skipUnless(
    HAS_RECORDINGS,
    "No recorded model responses. Create them with "
    "`manage.py grading_benchmark --mode record` (makes real, billed calls).",
)
class ReplayRunTest(BenchmarkFixtureMixin, TestCase):
    """The nightly regression: the whole pipeline on recorded responses."""

    def test_replay_grades_every_submission_without_errors(self):
        teacher = self._make_teacher()
        run = runner.execute_benchmark(teacher, mode=runner.MODE_REPLAY)

        expected_cases = len(ASSIGNMENTS) * len(STUDENTS)
        self.assertEqual(len(run["results"]), expected_cases)

        failed = [r for r in run["results"] if r["error"]]
        self.assertEqual(
            failed,
            [],
            "\n".join(
                f"{r['assignment_key']}/{r['student_key']}: {r['error']}"
                for r in failed
            ),
        )

    def test_replay_is_deterministic(self):
        # Two replays of the same recordings must produce identical
        # scores; anything else means non-determinism has crept into our
        # own code, since the model responses are fixed.
        teacher = self._make_teacher()
        first = scoring.score_run(
            runner.execute_benchmark(teacher, mode=runner.MODE_REPLAY)
        )
        second = scoring.score_run(
            runner.execute_benchmark(teacher, mode=runner.MODE_REPLAY)
        )
        self.assertEqual(first["overall"], second["overall"])
        self.assertEqual(
            [f["question_number"] for f in first["failures"]],
            [f["question_number"] for f in second["failures"]],
        )

    def test_deterministic_tier_is_never_wrong(self):
        # Tier 0 claims only unambiguous matches, so anything it claimed
        # and got wrong is a defect in objective_grading.py, not a model
        # quality issue.
        teacher = self._make_teacher()
        report = scoring.score_run(
            runner.execute_benchmark(teacher, mode=runner.MODE_REPLAY)
        )
        accuracy = report["deterministic"]["accuracy"]
        if accuracy is not None:
            self.assertEqual(accuracy, 1.0)

    def test_identical_answers_receive_identical_scores(self):
        teacher = self._make_teacher()
        run = runner.execute_benchmark(teacher, mode=runner.MODE_REPLAY)
        probes = scoring.check_consistency(run, IDENTICAL_ANSWER_PROBES)
        inconsistent = [p for p in probes if p["consistent"] is False]
        self.assertEqual(
            inconsistent,
            [],
            f"byte-identical answers scored differently: {inconsistent}",
        )


@override_settings(ENABLE_AI_LIVE_QA=False)
class ScheduledTaskTest(SimpleTestCase):
    def test_live_task_is_a_no_op_when_disabled(self):
        from ai_processor.tasks import weekly_grading_benchmark_live

        result = weekly_grading_benchmark_live.apply().get()
        self.assertIn("not enabled", result)


@override_settings(
    GRADING_SECOND_OPINION_ENABLED=False,
    GRADING_EVIDENCE_ENFORCEMENT="strict",
    # Otherwise a prior test run's cached evaluation for this exact
    # question+answer content would short-circuit the AI call this test
    # exists to exercise. See grading_cache.py and the same note in
    # SECOND_OPINION_SETTINGS (tests_second_opinion_pipeline.py).
    GRADING_ANSWER_CACHE_ENABLED=False,
)
class EvidenceDegradesOnFinalAttemptTest(SimpleTestCase):
    """
    Regression coverage for FINDING 1 (see benchmark/FINDINGS.md).

    The first live benchmark run failed one submission in 21 outright:
    on long multi-step algebra the model quotes by eliding intermediate
    steps, which is textually not verbatim, so strict evidence rejected
    every attempt and the student received NO GRADE.

    Strict rejection is right on attempts 1..n-1 — a re-ask usually
    produces a proper quote. On the LAST attempt it has to degrade to
    'log' instead, because a grade carrying one unverified quote is
    strictly better for the student than no grade at all.
    """

    def setUp(self):
        from ai_processor.services import AIProcessor

        self.processor = AIProcessor()
        self.questions = [
            {
                "question_number": 1,
                "question_text": "Derive it.",
                "question_type": "SHORT-ANSWER",
                "points": 10,
                "options": [],
                "rubric": [
                    {"level": "excellent", "points": 10, "description": ""},
                    {"level": "good", "points": 7, "description": ""},
                    {"level": "fair", "points": 4, "description": ""},
                    {"level": "poor", "points": 0, "description": ""},
                ],
                "model_answer": "",
            }
        ]
        self.answers = [
            {"question_number": 1, "answer_html": "<p>step one then step two</p>"}
        ]

    def _respond(self, **kwargs):
        import json as _json
        from unittest.mock import MagicMock

        payload = {
            "question_evaluations": [
                {
                    "question_number": 1,
                    "score_awarded": 7,
                    "max_points": 10,
                    # Elided quote: faithful in meaning, not verbatim —
                    # exactly what the live model produced on maths.
                    "evidence_quotes": ["step one ... step two"],
                }
            ],
            "grading_summary": {},
            "grading_confidence": 90,
            "overall_performance_analysis": "ok",
            "recommendations": [],
        }
        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = _json.dumps(payload)
        response.usage.total_tokens = 10
        response.model = "test-model"
        return response

    def test_non_final_attempt_still_rejects_unverifiable_evidence(self):
        from unittest.mock import patch

        from ai_processor.services import AIProcessor, GradingEvidenceError

        with patch.object(
            AIProcessor, "execute_graded_task", side_effect=self._respond
        ):
            with self.assertRaises(GradingEvidenceError):
                self.processor._grade_student_submission_impl(
                    user=__import__(
                        "unittest.mock", fromlist=["MagicMock"]
                    ).MagicMock(),
                    rubric_json=self.questions,
                    answer_json=self.answers,
                    final_attempt=False,
                )

    def test_final_attempt_degrades_and_still_returns_a_grade(self):
        from unittest.mock import MagicMock, patch

        from ai_processor.services import AIProcessor

        with patch.object(
            AIProcessor, "execute_graded_task", side_effect=self._respond
        ):
            result = self.processor._grade_student_submission_impl(
                user=MagicMock(),
                rubric_json=self.questions,
                answer_json=self.answers,
                final_attempt=True,
            )

        # The student gets their grade...
        self.assertEqual(result["grading_summary"]["total_score"], 7)
        evaluation = result["question_evaluations"][0]
        # ...and the unverified quote is still flagged as such, so the
        # teacher can tell the difference.
        self.assertFalse(evaluation.get("evidence_verified", True))

    def test_extract_grade_with_retry_marks_only_its_last_attempt_final(self):
        from unittest.mock import MagicMock, patch

        from ai_processor.services import AIProcessor

        seen = []

        def record(*args, **kwargs):
            seen.append(kwargs.get("final_attempt"))
            raise RuntimeError("boom")

        with patch.object(AIProcessor, "grade_student_submission", side_effect=record):
            with self.assertRaisesRegex(Exception, "All 3 attempts failed"):
                self.processor.extract_grade_with_retry(
                    MagicMock(), self.questions, self.answers, max_retries=3
                )

        self.assertEqual(seen, [False, False, True])
