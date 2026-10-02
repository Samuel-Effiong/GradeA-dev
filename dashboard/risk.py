"""Single source of truth for "at-risk student" classification.

Used by the weekly teacher course-summary email, the school-admin
dashboard/weekly digest/daily alert task, and the teacher-admin session
and per-course student dashboards, so all consumers agree on one
definition instead of drifting independently.
"""

from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

TREND_IMPROVING = "IMPROVING"
TREND_DECLINING = "DECLINING"
TREND_STABLE = "STABLE"
TREND_INSUFFICIENT_DATA = "INSUFFICIENT DATA"


@dataclass
class RiskInputs:
    expected_assignment_count: int
    submitted_count: int
    # (submission_date, score_percentage) pairs for graded submissions, any order.
    graded_scores: list = field(default_factory=list)


@dataclass
class RiskResult:
    average_grade: float | None
    submission_rate: float
    grade_trend: str
    trend_delta: float
    at_risk: bool
    issue_tags: list
    reasons: list


class StudentRiskEvaluator:
    """at_risk = critical_grade OR critical_missing_work OR (moderate_flags >= 2)

    critical_grade:        average_grade is not None and average_grade < CRITICAL_GRADE_THRESHOLD
    critical_missing_work: expected_assignment_count >= 2 and submission_rate < CRITICAL_SUBMISSION_THRESHOLD
    moderate flags (need 2 of 3):
      A. average_grade is not None and average_grade < MODERATE_GRADE_THRESHOLD
      B. submission_rate < SUBMISSION_RISK_THRESHOLD
      C. grade_trend == DECLINING
    """

    CRITICAL_GRADE_THRESHOLD = 60.0
    MODERATE_GRADE_THRESHOLD = 70.0
    SUBMISSION_RISK_THRESHOLD = 0.70
    CRITICAL_SUBMISSION_THRESHOLD = 0.50

    TREND_WINDOW = 6
    TREND_DEADBAND = 3.0

    def evaluate(self, inputs: RiskInputs) -> RiskResult:
        expected = inputs.expected_assignment_count
        submitted = inputs.submitted_count
        submission_rate = submitted / expected if expected else 1.0

        graded_scores = sorted(inputs.graded_scores, key=lambda pair: pair[0])
        scores_only = [score for _, score in graded_scores]
        average_grade = (
            round(sum(scores_only) / len(scores_only), 2) if scores_only else None
        )

        grade_trend, trend_delta = self._calculate_grade_trend(graded_scores)

        issue_tags = []
        reasons = []

        if expected and submitted == 0:
            issue_tags.append("missing_submissions")
            reasons.append("No submitted work for assignments due so far.")
        elif expected and submission_rate < self.SUBMISSION_RISK_THRESHOLD:
            issue_tags.append("missing_submissions")
            missing_count = expected - submitted
            reasons.append(
                f"Submission rate is {round(submission_rate * 100, 1)}% with "
                f"{missing_count} missing assignment(s)."
            )

        if average_grade is not None and average_grade < self.MODERATE_GRADE_THRESHOLD:
            if submission_rate >= self.SUBMISSION_RISK_THRESHOLD:
                issue_tags.append("conceptual_gaps")
                reasons.append(
                    f"Average graded score is {average_grade}%, which suggests "
                    "conceptual gaps despite regular submission."
                )
            else:
                issue_tags.append("low_scores")
                reasons.append(
                    f"Average graded score is {average_grade}%, below the "
                    f"{self.MODERATE_GRADE_THRESHOLD:.0f}% target."
                )

        if grade_trend == TREND_DECLINING:
            issue_tags.append("declining_performance")
            reasons.append("Recent graded work is trending downward.")

        moderate_flags = 0
        if expected and submission_rate < self.SUBMISSION_RISK_THRESHOLD:
            moderate_flags += 1
        if average_grade is not None and average_grade < self.MODERATE_GRADE_THRESHOLD:
            moderate_flags += 1
        if grade_trend == TREND_DECLINING:
            moderate_flags += 1

        critical_grade = (
            average_grade is not None and average_grade < self.CRITICAL_GRADE_THRESHOLD
        )
        critical_missing_work = (
            expected >= 2 and submission_rate < self.CRITICAL_SUBMISSION_THRESHOLD
        )

        at_risk = critical_grade or critical_missing_work or moderate_flags >= 2

        return RiskResult(
            average_grade=average_grade,
            submission_rate=round(submission_rate * 100, 2),
            grade_trend=grade_trend,
            trend_delta=trend_delta,
            at_risk=at_risk,
            issue_tags=issue_tags,
            reasons=reasons,
        )

    def _calculate_grade_trend(self, graded_scores):
        """Least-squares slope over the last TREND_WINDOW graded scores,
        projected across the window's date span, classified against a
        deadband. Using every point (rather than just the first/last score)
        makes the result far less sensitive to any single outlier."""
        recent = graded_scores[-self.TREND_WINDOW :]
        if len(recent) < 2:
            return TREND_INSUFFICIENT_DATA, 0.0

        first_day = self._as_date(recent[0][0])
        days = np.array(
            [(self._as_date(day) - first_day).days for day, _ in recent],
            dtype=float,
        )
        scores = np.array([score for _, score in recent], dtype=float)

        span_days = days[-1] - days[0]
        if span_days == 0:
            # All graded the same day: no time axis to fit a slope against,
            # fall back to a simple first-vs-last comparison.
            delta = round(float(scores[-1] - scores[0]), 2)
        else:
            slope, _ = np.polyfit(days, scores, 1)
            delta = round(float(slope) * span_days, 2)

        if delta >= self.TREND_DEADBAND:
            return TREND_IMPROVING, delta
        if delta <= -self.TREND_DEADBAND:
            return TREND_DECLINING, delta
        return TREND_STABLE, delta

    @staticmethod
    def _as_date(value):
        if isinstance(value, datetime):
            return value.date()
        return value
