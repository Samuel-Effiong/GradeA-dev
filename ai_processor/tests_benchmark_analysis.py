"""
Coverage for benchmark trend analysis.

These tests use hand-built numbers rather than real runs, so the maths is
checked against values that can be verified by hand.

The two most important cases are the ones that would silently produce
confident nonsense:

- Replay runs must be excluded, because they are deterministic. Including
  them collapses the apparent spread towards zero, after which every genuine
  run looks like a dramatic anomaly.
- Fewer than three runs must produce "not enough data", not a spread computed
  from two points that reads as authoritative.

Run with:
    python manage.py test ai_processor.tests_benchmark_analysis
"""

from django.test import SimpleTestCase

from ai_processor.benchmark import analysis


def _run(run_id, mode="record", exact=0.85, full=True, prompt="p1", **metrics):
    return {
        "run_id": run_id,
        "mode": mode,
        "is_full_run": full,
        "prompt_fingerprint": prompt,
        "metrics": {"exact_rate": exact, **metrics},
    }


class ComparableRunsTest(SimpleTestCase):
    def test_replay_runs_are_excluded_by_default(self):
        runs = [_run("1", mode="record"), _run("2", mode="replay")]
        self.assertEqual([r["run_id"] for r in analysis.comparable_runs(runs)], ["1"])

    def test_replay_can_be_included_explicitly(self):
        runs = [_run("1", mode="record"), _run("2", mode="replay")]
        self.assertEqual(len(analysis.comparable_runs(runs, include_replay=True)), 2)

    def test_partial_runs_are_excluded_by_default(self):
        runs = [_run("1"), _run("2", full=False)]
        self.assertEqual([r["run_id"] for r in analysis.comparable_runs(runs)], ["1"])

    def test_runs_are_returned_in_chronological_order(self):
        # run_id starts with a UTC timestamp, so string order is time order.
        runs = [_run("20260301T000000Z-b"), _run("20260101T000000Z-a")]
        self.assertEqual(
            [r["run_id"] for r in analysis.comparable_runs(runs)],
            ["20260101T000000Z-a", "20260301T000000Z-b"],
        )

    def test_including_replay_would_distort_the_spread(self):
        # The trap this default exists to avoid, made explicit: four
        # identical deterministic replays alongside genuinely varying paid
        # runs drag the spread down and would make normal variation look
        # like an anomaly.
        paid = [
            _run(f"p{i}", mode="record", exact=v)
            for i, v in enumerate([0.80, 0.85, 0.90])
        ]
        replays = [_run(f"r{i}", mode="replay", exact=0.85) for i in range(20)]

        paid_only = analysis.noise_band(
            [
                v
                for _, v in analysis.metric_values(
                    analysis.comparable_runs(paid + replays), "exact_rate"
                )
            ]
        )
        with_replays = analysis.noise_band(
            [
                v
                for _, v in analysis.metric_values(
                    analysis.comparable_runs(paid + replays, include_replay=True),
                    "exact_rate",
                )
            ]
        )
        self.assertGreater(
            paid_only["spread"],
            with_replays["spread"],
            "replays must not be allowed to shrink the apparent spread",
        )


class NoiseBandTest(SimpleTestCase):
    def test_too_few_runs_refuses_to_state_a_range(self):
        self.assertIsNone(analysis.noise_band([]))
        self.assertIsNone(analysis.noise_band([0.8]))
        self.assertIsNone(analysis.noise_band([0.8, 0.9]))

    def test_band_is_computed_from_three_or_more(self):
        band = analysis.noise_band([0.80, 0.85, 0.90])
        self.assertEqual(band["runs"], 3)
        self.assertEqual(band["mean"], 0.85)
        self.assertEqual(band["min"], 0.80)
        self.assertEqual(band["max"], 0.90)
        # population stdev of (.80,.85,.90) = 0.040824...
        self.assertAlmostEqual(band["spread"], 0.0408, places=3)

    def test_nones_are_ignored(self):
        self.assertEqual(analysis.noise_band([0.8, None, 0.85, 0.9])["runs"], 3)

    def test_rate_bounds_are_clamped_for_display(self):
        # mean +/- 2 spread is the right sum but can exceed what a rate can
        # be. A "normal range" topping out above 100% reads as a bug and
        # undermines trust in the rest of the report.
        band = analysis.noise_band([0.99, 1.0, 1.0, 1.0], kind="rate")
        self.assertLessEqual(band["normal_high"], 1.0)
        self.assertGreaterEqual(band["normal_low"], 0.0)

    def test_non_rate_metrics_are_not_clamped(self):
        # Token counts have no ceiling at 1.
        band = analysis.noise_band([500000, 600000, 700000], kind="count")
        self.assertGreater(band["normal_high"], 1.0)


class AssessLatestTest(SimpleTestCase):
    def test_needs_history_before_judging(self):
        self.assertIsNone(analysis.assess_latest([0.8, 0.85, 0.9]))

    def test_ordinary_wobble_is_not_flagged(self):
        # Latest sits comfortably inside the earlier spread.
        result = analysis.assess_latest([0.80, 0.85, 0.90, 0.86])
        self.assertFalse(result["unusual"])
        self.assertEqual(result["baseline_runs"], 3)

    def test_large_move_is_flagged(self):
        # Earlier runs are tightly clustered; the latest is far outside.
        result = analysis.assess_latest([0.80, 0.81, 0.80, 0.95])
        self.assertTrue(result["unusual"])
        self.assertEqual(result["direction"], "up")
        self.assertGreater(result["sigmas"], 2)

    def test_baseline_excludes_the_value_being_judged(self):
        # If the latest value were included it would drag the mean towards
        # itself and hide the very change being looked for.
        result = analysis.assess_latest([0.80, 0.80, 0.80, 0.95])
        self.assertEqual(result["baseline_mean"], 0.80)
        self.assertEqual(result["baseline_runs"], 3)

    def test_identical_history_reports_direction_without_dividing_by_zero(self):
        result = analysis.assess_latest([0.80, 0.80, 0.80, 0.90])
        self.assertTrue(result["unusual"])
        self.assertIsNone(result["sigmas"])
        self.assertEqual(result["direction"], "up")

    def test_identical_history_and_no_change_is_not_unusual(self):
        result = analysis.assess_latest([0.80, 0.80, 0.80, 0.80])
        self.assertFalse(result["unusual"])
        self.assertEqual(result["direction"], "flat")


class TrendsTest(SimpleTestCase):
    def test_reports_each_metric_and_flags_mixed_prompts(self):
        runs = [
            _run("1", exact=0.80, prompt="p1", evidence_verified_rate=0.90),
            _run("2", exact=0.85, prompt="p1", evidence_verified_rate=0.92),
            _run("3", exact=0.90, prompt="p2", evidence_verified_rate=0.99),
        ]
        report = analysis.trends(runs)
        self.assertEqual(report["runs_considered"], 3)
        # Comparing across a prompt change is not like-for-like; say so.
        self.assertTrue(report["mixed_prompt_versions"])
        self.assertEqual(report["metrics"]["exact_rate"]["band"]["runs"], 3)
        self.assertEqual(len(report["metrics"]["evidence_verified_rate"]["series"]), 3)

    def test_single_prompt_version_is_not_flagged(self):
        runs = [_run(str(i), exact=0.8, prompt="p1") for i in range(3)]
        self.assertFalse(analysis.trends(runs)["mixed_prompt_versions"])

    def test_missing_metric_is_simply_absent_not_an_error(self):
        report = analysis.trends([_run("1"), _run("2"), _run("3")])
        self.assertEqual(report["metrics"]["total_tokens"]["series"], [])
        self.assertIsNone(report["metrics"]["total_tokens"]["band"])


def _q(run_id, level, akey="maths", skey="strong", qnum=4, points=None):
    return {
        "run_id": run_id,
        "assignment_key": akey,
        "student_key": skey,
        "question_number": qnum,
        "awarded_level": level,
        "awarded_points": points,
        "expected_level": 0,
        "question_type": "SHORT-ANSWER",
    }


class QuestionStabilityTest(SimpleTestCase):
    def test_question_graded_the_same_every_run_is_stable(self):
        rows = [_q("1", 0), _q("2", 0), _q("3", 0)]
        finding = analysis.question_stability(rows)[0]
        self.assertFalse(finding["unstable"])
        self.assertEqual(finding["distinct_levels"], 1)
        self.assertEqual(finding["runs"], 3)

    def test_question_whose_grade_moves_is_flagged(self):
        # The capability no existing metric provides: the same answer,
        # graded differently on different runs.
        rows = [_q("1", 0), _q("2", 1), _q("3", 0)]
        finding = analysis.question_stability(rows)[0]
        self.assertTrue(finding["unstable"])
        self.assertEqual(finding["distinct_levels"], 2)

    def test_most_unstable_questions_are_reported_first(self):
        rows = [
            _q("1", 0, qnum=1),
            _q("2", 0, qnum=1),
            _q("1", 0, qnum=2),
            _q("2", 1, qnum=2),
            _q("3", 2, qnum=2),
        ]
        findings = analysis.question_stability(rows)
        self.assertEqual(findings[0]["question_number"], 2)
        self.assertEqual(findings[0]["distinct_levels"], 3)

    def test_question_seen_only_once_is_not_reported(self):
        # Nothing to compare against yet.
        self.assertEqual(analysis.question_stability([_q("1", 0)]), [])

    def test_questions_are_grouped_per_student_not_merged(self):
        rows = [
            _q("1", 0, skey="strong"),
            _q("2", 0, skey="strong"),
            _q("1", 2, skey="weak"),
            _q("2", 2, skey="weak"),
        ]
        findings = analysis.question_stability(rows)
        self.assertEqual(len(findings), 2)
        self.assertTrue(all(not f["unstable"] for f in findings))

    def test_can_be_limited_to_specific_runs(self):
        rows = [_q("1", 0), _q("2", 1), _q("3", 0)]
        findings = analysis.question_stability(rows, run_ids={"1", "3"})
        self.assertEqual(findings[0]["runs"], 2)
        self.assertFalse(findings[0]["unstable"])


class QuestionHistoryTest(SimpleTestCase):
    def test_returns_one_question_oldest_first(self):
        rows = [_q("3", 1), _q("1", 0), _q("2", 0), _q("1", 2, qnum=9)]
        found = analysis.question_history(rows, "maths", "strong", 4)
        self.assertEqual([r["run_id"] for r in found], ["1", "2", "3"])

    def test_question_number_matches_across_string_and_int(self):
        rows = [_q("1", 0, qnum=4)]
        self.assertEqual(
            len(analysis.question_history(rows, "maths", "strong", "4")), 1
        )
