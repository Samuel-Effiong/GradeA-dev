"""Tests for academic rigor scoring.

Split into four layers:

  * RigorScoringTest         - the pure functions in assignments/rigor.py
  * RigorMalformedInputTest  - hostile/garbage question payloads
  * AssignmentRigorSyncTest  - the pre_save hook keeping the columns fresh
  * BloomsLevelValidatorTest - the serializer bug that used to strip the data
  * BackfillRigorCommandTest - the ops recompute path
"""

from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from assignments.models import Assignment, AssignmentStatus
from assignments.rigor import (
    MIN_BLOOMS_COVERAGE,
    compose_rigor,
    compute_demand,
    compute_evidence,
    compute_standards,
    score_assignment,
)
from assignments.serializers import QuestionSerializer
from classrooms.models import Course, Session
from users.models import CustomUser, UserTypes


def question(
    *,
    points=10,
    blooms="Apply",
    qtype="OBJECTIVE",
    rubric_levels=0,
    number=1,
):
    """Build a question payload in the shape the AI extraction schema emits."""
    payload = {
        "question_number": number,
        "question_text": f"Question {number}",
        "question_type": qtype,
        "points": points,
        "options": [],
        "rubric": [
            {"level": f"L{i}", "description": "d", "points": float(i)}
            for i in range(rubric_levels)
        ],
        "model_answer": "",
    }
    if blooms is not None:
        payload["blooms_level"] = blooms
    return payload


class RigorScoringTest(SimpleTestCase):
    def test_demand_maps_blooms_levels_onto_the_five_point_scale(self):
        for level, expected in [
            ("Remember", 0.0),
            ("Understand", 1.0),
            ("Apply", 2.0),
            ("Analyze", 3.0),
            ("Evaluate", 4.0),
            ("Create", 5.0),
        ]:
            demand, coverage = compute_demand([question(blooms=level)])
            self.assertEqual(demand, expected, level)
            self.assertEqual(coverage, 1.0)

    def test_demand_is_weighted_by_question_points(self):
        # A 30-point "Create" should dominate a 10-point "Remember":
        # (10*0 + 30*5) / 40 = 3.75. An unweighted mean would give 2.5.
        demand, coverage = compute_demand(
            [
                question(points=10, blooms="Remember", number=1),
                question(points=30, blooms="Create", number=2),
            ]
        )
        self.assertAlmostEqual(demand, 3.75)
        self.assertEqual(coverage, 1.0)

    def test_demand_is_case_and_spelling_tolerant(self):
        demand, _ = compute_demand([question(blooms="  aNaLyse ")])
        self.assertEqual(demand, 3.0)

    def test_demand_falls_back_to_unweighted_mean_when_no_points(self):
        # Rubric-only assignments carry no point values; weighting by points
        # would divide by zero and drop them from the metric entirely.
        demand, coverage = compute_demand(
            [
                question(points=0, blooms="Remember", number=1),
                question(points=0, blooms="Create", number=2),
            ]
        )
        self.assertEqual(demand, 2.5)
        self.assertEqual(coverage, 1.0)

    def test_demand_is_none_below_the_coverage_floor(self):
        # Only 1 of 3 equal-weight questions is rated: 0.33 < 0.5.
        demand, coverage = compute_demand(
            [
                question(blooms="Create", number=1),
                question(blooms=None, number=2),
                question(blooms=None, number=3),
            ]
        )
        self.assertIsNone(demand)
        self.assertAlmostEqual(coverage, 1 / 3)

    def test_demand_is_reported_exactly_at_the_coverage_floor(self):
        demand, coverage = compute_demand(
            [
                question(blooms="Create", number=1),
                question(blooms=None, number=2),
            ]
        )
        self.assertEqual(coverage, MIN_BLOOMS_COVERAGE)
        self.assertEqual(demand, 5.0)

    def test_demand_ignores_unrecognised_levels(self):
        demand, coverage = compute_demand(
            [
                question(blooms="Create", number=1),
                question(blooms="Synthesise", number=2),  # not in the taxonomy
            ]
        )
        self.assertEqual(coverage, 0.5)
        self.assertEqual(demand, 5.0)

    def test_demand_is_none_for_an_empty_assignment(self):
        self.assertEqual(compute_demand([]), (None, 0.0))
        self.assertEqual(compute_demand(None), (None, 0.0))

    def test_standards_scores_rubric_coverage_on_open_questions(self):
        questions = [
            question(qtype="ESSAY", rubric_levels=4, number=1),
            question(qtype="SHORT-ANSWER", rubric_levels=3, number=2),
            question(qtype="ESSAY", rubric_levels=0, number=3),
            question(qtype="ESSAY", rubric_levels=2, number=4),  # too shallow
        ]
        # 2 of 4 open questions carry a usable rubric -> 5 * 0.5
        self.assertEqual(compute_standards(questions), 2.5)

    def test_standards_ignores_objective_questions(self):
        # An objective question without a rubric is not a failure of standards,
        # so it must not drag the score down.
        questions = [
            question(qtype="ESSAY", rubric_levels=3, number=1),
            question(qtype="OBJECTIVE", rubric_levels=0, number=2),
        ]
        self.assertEqual(compute_standards(questions), 5.0)

    def test_standards_is_none_when_there_are_no_open_questions(self):
        self.assertIsNone(compute_standards([question(qtype="OBJECTIVE")]))
        self.assertIsNone(compute_standards([]))

    def test_evidence_inverts_achieved_percentage(self):
        self.assertEqual(compute_evidence(100), 0.0)
        self.assertEqual(compute_evidence(0), 5.0)
        self.assertEqual(compute_evidence(60), 2.0)
        self.assertEqual(compute_evidence(Decimal("60.00")), 2.0)

    def test_evidence_clamps_out_of_range_percentages(self):
        self.assertEqual(compute_evidence(140), 0.0)
        self.assertEqual(compute_evidence(-20), 5.0)

    def test_evidence_is_none_without_data(self):
        self.assertIsNone(compute_evidence(None))
        self.assertIsNone(compute_evidence("not a number"))

    def test_compose_blends_all_three_components_by_weight(self):
        # 0.6*4 + 0.25*2 + 0.15*3 = 3.35
        self.assertAlmostEqual(compose_rigor(4.0, 2.0, 3.0), 3.35)

    def test_compose_renormalizes_over_available_components(self):
        # Demand alone is the whole score, not 60% of it.
        self.assertAlmostEqual(compose_rigor(4.0), 4.0)
        # (0.6*4 + 0.25*2) / 0.85
        self.assertAlmostEqual(compose_rigor(4.0, 2.0), 2.9 / 0.85)
        # (0.6*4 + 0.15*3) / 0.75
        self.assertAlmostEqual(compose_rigor(4.0, None, 3.0), 2.85 / 0.75)

    def test_compose_requires_demand(self):
        # An outcome-only number answers a different question and must not be
        # published under the same label.
        self.assertIsNone(compose_rigor(None, 5.0, 5.0))

    def test_compose_clamps_into_range(self):
        self.assertEqual(compose_rigor(99.0), 5.0)
        self.assertEqual(compose_rigor(-4.0), 0.0)

    def test_score_assignment_returns_the_denormalized_triple(self):
        demand, standards, coverage = score_assignment(
            [question(qtype="ESSAY", blooms="Create", rubric_levels=3)]
        )
        self.assertEqual(demand, 5.0)
        self.assertEqual(standards, 5.0)
        self.assertEqual(coverage, 1.0)


class RigorMalformedInputTest(SimpleTestCase):
    """Assignment.questions is a free-form JSONField fed by an AI pipeline, so
    every one of these shapes is reachable in production. None may raise."""

    def test_non_list_payloads_score_as_unavailable(self):
        for payload in [None, {}, "questions", 42, object()]:
            self.assertEqual(compute_demand(payload), (None, 0.0))
            self.assertIsNone(compute_standards(payload))

    def test_non_dict_entries_are_skipped(self):
        demand, coverage = compute_demand(["junk", None, question(blooms="Create")])
        self.assertEqual(demand, 5.0)
        self.assertEqual(coverage, 1.0)

    def test_garbage_point_values_are_treated_as_zero_weight(self):
        for bad in ["abc", None, float("nan"), float("inf"), -5]:
            demand, _ = compute_demand(
                [
                    question(points=bad, blooms="Remember", number=1),
                    question(points=10, blooms="Create", number=2),
                ]
            )
            # The bad question contributes no weight, so Create decides it.
            self.assertEqual(demand, 5.0, bad)

    def test_non_string_blooms_level_is_ignored(self):
        demand, coverage = compute_demand([{"points": 10, "blooms_level": 3}])
        self.assertIsNone(demand)
        self.assertEqual(coverage, 0.0)

    def test_malformed_rubric_does_not_count_as_a_rubric(self):
        questions = [
            {"question_type": "ESSAY", "points": 10, "rubric": "three levels"},
            {"question_type": "ESSAY", "points": 10, "rubric": [1, 2, 3]},
        ]
        self.assertEqual(compute_standards(questions), 0.0)


class RigorFixtureMixin:
    @classmethod
    def make_course(cls, suffix=""):
        teacher = CustomUser.objects.create_user(
            email=f"rigor-teacher{suffix}@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            first_name="Rigor",
            last_name="Teacher",
        )
        session = Session.objects.create(name=f"Term{suffix}", teacher=teacher)
        return Course.objects.create(
            name=f"Course{suffix}", teacher=teacher, session=session
        )


class AssignmentRigorSyncTest(RigorFixtureMixin, TestCase):
    def setUp(self):
        self.course = self.make_course()

    def test_creating_an_assignment_populates_the_rigor_columns(self):
        assignment = Assignment.objects.create(
            title="Sources essay",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            questions=[question(qtype="ESSAY", blooms="Evaluate", rubric_levels=3)],
        )
        assignment.refresh_from_db()

        self.assertEqual(assignment.rigor_demand, 4.0)
        self.assertEqual(assignment.rigor_standards, 5.0)
        self.assertEqual(assignment.rigor_blooms_coverage, 1.0)

    def test_editing_questions_rescores_the_assignment(self):
        assignment = Assignment.objects.create(
            title="Recall quiz",
            course=self.course,
            questions=[question(blooms="Remember")],
        )
        self.assertEqual(assignment.rigor_demand, 0.0)

        assignment.questions = [question(blooms="Create")]
        assignment.save()
        assignment.refresh_from_db()

        self.assertEqual(assignment.rigor_demand, 5.0)

    def test_clearing_questions_clears_the_score(self):
        assignment = Assignment.objects.create(
            title="Quiz",
            course=self.course,
            questions=[question(blooms="Create")],
        )
        assignment.questions = None
        assignment.save()
        assignment.refresh_from_db()

        self.assertIsNone(assignment.rigor_demand)
        self.assertIsNone(assignment.rigor_standards)
        self.assertEqual(assignment.rigor_blooms_coverage, 0.0)

    def test_partial_save_that_excludes_questions_leaves_the_score_alone(self):
        assignment = Assignment.objects.create(
            title="Quiz",
            course=self.course,
            questions=[question(blooms="Create")],
        )
        # Simulate drift: a stale value in the column, then a partial save that
        # does not touch `questions`. Recomputing would not be persisted by
        # that UPDATE anyway, so the hook must not waste the work.
        Assignment.objects.filter(pk=assignment.pk).update(rigor_demand=1.0)
        assignment.refresh_from_db()
        assignment.title = "Renamed"
        assignment.save(update_fields=["title"])
        assignment.refresh_from_db()

        self.assertEqual(assignment.rigor_demand, 1.0)
        self.assertEqual(assignment.title, "Renamed")

    def test_an_assignment_with_no_blooms_data_scores_null_not_zero(self):
        # The regression that mattered most in the old formula: "no data"
        # must not render as the worst possible score.
        assignment = Assignment.objects.create(
            title="Legacy import",
            course=self.course,
            questions=[question(blooms=None)],
        )
        self.assertIsNone(assignment.rigor_demand)


class BloomsLevelValidatorTest(SimpleTestCase):
    """The validator used to raise on invalid input but forget to return
    valid input, so DRF stored None for every correctly-labelled question --
    silently deleting the signal rigor scoring is built on."""

    def test_valid_level_survives_validation(self):
        serializer = QuestionSerializer(data=question(blooms="Analyze"))
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data["blooms_level"], "Analyze")

    def test_every_allowed_level_survives_validation(self):
        for level in QuestionSerializer.BLOOMS_LEVEL:
            serializer = QuestionSerializer(data=question(blooms=level))
            self.assertTrue(serializer.is_valid(), serializer.errors)
            self.assertEqual(serializer.validated_data["blooms_level"], level)

    def test_blank_level_is_accepted_as_the_field_declares(self):
        serializer = QuestionSerializer(data=question(blooms=""))
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data["blooms_level"], "")

    def test_invalid_level_is_still_rejected(self):
        serializer = QuestionSerializer(data=question(blooms="Synthesise"))
        self.assertFalse(serializer.is_valid())
        self.assertIn("blooms_level", serializer.errors)


class BackfillRigorCommandTest(RigorFixtureMixin, TestCase):
    def setUp(self):
        self.course = self.make_course()
        self.assignment = Assignment.objects.create(
            title="Essay",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            questions=[question(qtype="ESSAY", blooms="Create", rubric_levels=3)],
        )

    def _run(self, **options):
        out = StringIO()
        call_command("backfill_assignment_rigor", stdout=out, stderr=out, **options)
        return out.getvalue()

    def test_command_repairs_drifted_rows(self):
        # .update() bypasses pre_save, which is exactly the drift this command
        # exists to repair.
        Assignment.objects.filter(pk=self.assignment.pk).update(
            rigor_demand=None, rigor_standards=None, rigor_blooms_coverage=None
        )

        output = self._run()
        self.assignment.refresh_from_db()

        self.assertEqual(self.assignment.rigor_demand, 5.0)
        self.assertEqual(self.assignment.rigor_standards, 5.0)
        self.assertIn("updated", output)

    def test_command_is_idempotent(self):
        self._run()
        output = self._run()
        # Nothing drifted the second time, so nothing should be rewritten.
        self.assertIn("updated      : 0", output)

    def test_dry_run_writes_nothing(self):
        Assignment.objects.filter(pk=self.assignment.pk).update(rigor_demand=None)

        output = self._run(dry_run=True)
        self.assignment.refresh_from_db()

        self.assertIsNone(self.assignment.rigor_demand)
        self.assertIn("would update", output)
        self.assertIn("dry run", output)


class RepairBloomsLevelsCommandTest(RigorFixtureMixin, TestCase):
    """Recovery of blooms_level that the serializer bug nulled out.

    The level survives in ai_raw_payload (the untouched AI response), so
    historical assignments are scoreable again rather than permanently blank.
    """

    def setUp(self):
        self.course = self.make_course()

    def _make(self, stored_levels, payload_levels, **kwargs):
        questions = [
            question(blooms=level, number=i + 1)
            for i, level in enumerate(stored_levels)
        ]
        payload = {
            "questions": [
                question(blooms=level, number=i + 1)
                for i, level in enumerate(payload_levels)
            ]
        }
        return Assignment.objects.create(
            title=kwargs.pop("title", "Recoverable"),
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            questions=questions,
            ai_raw_payload=payload,
            **kwargs,
        )

    def _run(self, **options):
        out = StringIO()
        call_command("repair_question_blooms_levels", stdout=out, stderr=out, **options)
        return out.getvalue()

    def test_missing_levels_are_restored_from_the_ai_payload(self):
        assignment = self._make([None, None], ["Analyze", "Create"])
        self.assertIsNone(assignment.rigor_demand)

        self._run()
        assignment.refresh_from_db()

        self.assertEqual(
            [q["blooms_level"] for q in assignment.questions],
            ["Analyze", "Create"],
        )
        # Rescored inline: (3 + 5) / 2
        self.assertEqual(assignment.rigor_demand, 4.0)

    def test_existing_levels_are_never_overwritten(self):
        # A teacher may have corrected a level by hand; the AI payload is not
        # more authoritative than that.
        assignment = self._make(["Remember"], ["Create"])

        self._run()
        assignment.refresh_from_db()

        self.assertEqual(assignment.questions[0]["blooms_level"], "Remember")

    def test_levels_are_matched_by_question_number_not_position(self):
        assignment = self._make([None, None], ["Create", "Remember"])
        # Reverse the stored order: numbers must still drive the match.
        assignment.questions = list(reversed(assignment.questions))
        assignment.save()

        self._run()
        assignment.refresh_from_db()

        by_number = {
            q["question_number"]: q["blooms_level"] for q in assignment.questions
        }
        self.assertEqual(by_number, {1: "Create", 2: "Remember"})

    def test_invalid_payload_levels_are_ignored(self):
        assignment = self._make([None], ["Synthesise"])

        self._run()
        assignment.refresh_from_db()

        self.assertIsNone(assignment.questions[0].get("blooms_level"))
        self.assertIsNone(assignment.rigor_demand)

    def test_assignment_without_an_ai_payload_is_untouched(self):
        assignment = Assignment.objects.create(
            title="Hand written",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            questions=[question(blooms=None)],
            ai_raw_payload=None,
        )

        self._run()
        assignment.refresh_from_db()

        self.assertIsNone(assignment.questions[0].get("blooms_level"))

    def test_command_is_idempotent(self):
        self._make([None], ["Create"])

        self._run()
        output = self._run()

        self.assertIn("repaired           : 0 assignment(s)", output)

    def test_dry_run_writes_nothing(self):
        assignment = self._make([None], ["Create"])

        output = self._run(dry_run=True)
        assignment.refresh_from_db()

        self.assertIsNone(assignment.questions[0].get("blooms_level"))
        self.assertIn("would repair       : 1 assignment(s)", output)
        self.assertIn("dry run", output)

    def test_repair_does_not_fire_the_post_save_cascade(self):
        # bulk_update, not save(): a silent data repair must not re-send
        # "new assignment posted" notifications or rebuild periodic tasks.
        assignment = self._make([None], ["Create"])

        with patch("assignments.signals.schedule_auto_grading") as scheduler:
            self._run()

        scheduler.assert_not_called()
        assignment.refresh_from_db()
        self.assertEqual(assignment.questions[0]["blooms_level"], "Create")
