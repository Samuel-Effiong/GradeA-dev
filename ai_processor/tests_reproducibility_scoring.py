"""
Tests for scoring.score_reproducibility.

WHY THIS METRIC EXISTS

Every other metric in benchmark/scoring.py measures SEVERITY — how wrong
a grade is. None measured whether the grader is REPEATABLE, and the two
move independently. The Run 8 prompt edit (see benchmark/FINDINGS.md)
proved it: severity was flat on every axis the suite tracked, while
questions changing verdict between identical runs went from 14 to 19 of
168. With no metric watching that axis, the regression was measured,
written up, and accepted as benign.

So these tests are weighted toward the ways the metric could quietly
mislead:

  * reporting a flattering 0.0 when it has not actually measured
    anything (one run, or no overlapping questions);
  * counting a question as "stable" when it was simply observed fewer
    times, which is how an errored submission would hide instability.

Run with:
    python manage.py test ai_processor.tests_reproducibility_scoring
"""

from unittest.mock import patch

from django.test import SimpleTestCase

from ai_processor.benchmark.scoring import score_reproducibility


def rows(*verdicts_by_question):
    """Build the iter_question_outcomes rows for one run."""
    return [
        {
            "assignment_key": "maths",
            "student_key": "excellent",
            "question_number": number,
            "verdict": verdict,
        }
        for number, verdict in verdicts_by_question
    ]


def score(*runs_rows):
    """Run score_reproducibility over canned per-question rows."""
    runs = [{"_rows": r} for r in runs_rows]
    with patch(
        "ai_processor.benchmark.scoring.iter_question_outcomes",
        side_effect=lambda run: run["_rows"],
    ):
        return score_reproducibility(runs)


class NotMeasuredTest(SimpleTestCase):
    """A metric that cannot measure must say so, not report perfection."""

    def test_zero_runs_returns_none(self):
        self.assertIsNone(score())

    def test_one_run_returns_none_rather_than_zero(self):
        # THE TRAP. A single run trivially has no variation, so a naive
        # implementation reports 0.0 unstable — which reads as "perfectly
        # reproducible" when the truth is "never checked".
        self.assertIsNone(score(rows((1, "exact"), (2, "adjacent"))))

    def test_no_overlapping_questions_returns_none(self):
        self.assertIsNone(score(rows((1, "exact")), rows((2, "exact"))))


class StabilityTest(SimpleTestCase):
    def test_identical_runs_are_fully_stable(self):
        r = score(
            rows((1, "exact"), (2, "adjacent")),
            rows((1, "exact"), (2, "adjacent")),
        )
        self.assertEqual(r["unstable"], 0)
        self.assertEqual(r["unstable_rate"], 0.0)
        self.assertEqual(r["exactness_flips"], 0)

    def test_a_changed_verdict_is_counted(self):
        r = score(rows((1, "exact")), rows((1, "adjacent")))
        self.assertEqual(r["unstable"], 1)
        self.assertEqual(r["unstable_rate"], 1.0)

    def test_exactness_flip_tracked_separately_from_any_change(self):
        # Q1 stops being exact (consequential — the headline metric keys
        # on exactness). Q2 wobbles between two non-exact verdicts, which
        # is instability but does not change whether it was right.
        r = score(
            rows((1, "exact"), (2, "adjacent")),
            rows((1, "adjacent"), (2, "off")),
        )
        self.assertEqual(r["unstable"], 2)
        self.assertEqual(r["exactness_flips"], 1)

    def test_unstable_questions_are_named(self):
        r = score(rows((7, "exact")), rows((7, "off")))
        self.assertEqual(r["unstable_questions"], ["maths/excellent Q7"])

    def test_three_runs_catch_a_flip_a_pair_would_miss(self):
        # Runs 1 and 3 agree; only run 2 differs. Comparing just the
        # first and last would call this stable.
        r = score(rows((1, "exact")), rows((1, "off")), rows((1, "exact")))
        self.assertEqual(r["runs"], 3)
        self.assertEqual(r["unstable"], 1)

    def test_rates_are_over_comparable_questions(self):
        r = score(
            rows((1, "exact"), (2, "exact"), (3, "exact"), (4, "exact")),
            rows((1, "off"), (2, "exact"), (3, "exact"), (4, "exact")),
        )
        self.assertEqual(r["questions_compared"], 4)
        self.assertEqual(r["unstable_rate"], 0.25)


class PartialCoverageTest(SimpleTestCase):
    """
    A question missing from one run must be EXCLUDED, not treated as
    stable — otherwise an errored submission makes the grader look more
    reproducible than it is, which is the wrong direction to be wrong in.
    """

    def test_question_missing_from_one_run_is_skipped(self):
        r = score(
            rows((1, "exact"), (2, "exact")),
            rows((1, "off")),  # Q2 absent — submission errored
        )
        self.assertEqual(r["questions_compared"], 1)
        self.assertEqual(r["questions_skipped"], 1)

    def test_skipped_question_cannot_inflate_stability(self):
        # Q2 appears identically in two of three runs. Counting it would
        # add a "stable" question that was never actually re-observed.
        r = score(
            rows((1, "exact"), (2, "exact")),
            rows((1, "off"), (2, "exact")),
            rows((1, "exact")),
        )
        self.assertEqual(r["questions_compared"], 1)
        self.assertEqual(r["unstable_rate"], 1.0)

    def test_all_questions_present_means_nothing_skipped(self):
        r = score(rows((1, "exact"), (2, "exact")), rows((1, "exact"), (2, "exact")))
        self.assertEqual(r["questions_skipped"], 0)


class RunEightRegressionShapeTest(SimpleTestCase):
    """
    The metric must actually reproduce the shape of the finding that
    motivated it: same severity, different stability.
    """

    def test_same_exact_count_can_still_differ_in_stability(self):
        # Both configurations score 1 exact of 2 in every run, so any
        # severity metric reports them as identical. Only configuration
        # two is unstable.
        stable = score(
            rows((1, "exact"), (2, "off")),
            rows((1, "exact"), (2, "off")),
        )
        unstable = score(
            rows((1, "exact"), (2, "off")),
            rows((1, "off"), (2, "exact")),
        )
        self.assertEqual(stable["unstable"], 0)
        self.assertEqual(unstable["unstable"], 2)
