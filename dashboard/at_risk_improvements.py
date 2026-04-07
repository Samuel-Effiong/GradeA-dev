# """
# Enhanced At-Risk Student Detection Algorithms
#
# This module provides several improved approaches for identifying students who need intervention.
# Each method uses different statistical weighting and thresholds.
# """
#
# from dataclasses import dataclass
#
# # from datetime import timedelta
# from enum import Enum
# from typing import Dict, List, Tuple
#
# # from django.db.models import Avg, Case, Count, F, Q, Value, When
# # from django.db.models.functions import Coalesce
# # from django.utils import timezone
#
#
# class RiskLevel(Enum):
#     """Risk severity levels"""
#
#     NOT_AT_RISK = 0
#     LOW_RISK = 1
#     MODERATE_RISK = 2
#     HIGH_RISK = 3
#
#
# @dataclass
# class RiskMetrics:
#     """Container for risk calculation metrics"""
#
#     avg_grade: float
#     submission_rate: float
#     grade_trend: str
#     grade_variance: float  # Standard deviation of recent scores
#     momentum: float  # Rate of change in scores
#     weeks_active: int
#     recency_score: float  # How recent the student activity is
#
#
# class AtRiskCalculator:
#     """
#     Improved at-risk student calculator with multiple algorithms
#     """
#
#     # Configurable thresholds
#     GRADE_THRESHOLD = 50  # Below 50% is concerning
#     SUBMISSION_THRESHOLD = 0.7  # 70% submission rate
#     TREND_WINDOW = 5  # Last 5 submissions for trend
#     INACTIVITY_DAYS = 14  # Flagged if inactive for 2+ weeks
#
#     @staticmethod
#     def simple_threshold_method(metrics: RiskMetrics) -> bool:
#         """
#         CURRENT METHOD: Requires 2+ criteria to be concerning
#
#         Pros:
#         - Simple to understand and audit
#         - Prevents over-flagging
#
#         Cons:
#         - Binary (either at-risk or not)
#         - No nuance for borderline cases
#         - Doesn't weight criteria equally
#         """
#         flags = 0
#
#         if (
#             metrics.avg_grade is not None
#             and metrics.avg_grade < AtRiskCalculator.GRADE_THRESHOLD
#         ):
#             flags += 1
#
#         if metrics.submission_rate < AtRiskCalculator.SUBMISSION_THRESHOLD:
#             flags += 1
#
#         if metrics.grade_trend == "DECLINING":
#             flags += 1
#
#         return flags >= 2
#
#     @staticmethod
#     def weighted_score_method(metrics: RiskMetrics) -> Tuple[float, RiskLevel]:
#         """
#         METHOD 1: Weighted Risk Score (0-100)
#
#         Assigns weighted scores to each metric and combines them.
#         More nuanced than binary approach.
#
#         Benefits:
#         - Provides risk score for sorting/prioritization
#         - Can identify borderline cases
#         - More sensitive to data changes
#         """
#         risk_score = 0
#
#         # Grade component (40% weight)
#         if metrics.avg_grade is not None:
#             grade_risk = max(0, (100 - metrics.avg_grade) / 100)  # 0-1 scale
#             risk_score += grade_risk * 40
#
#         # Submission component (30% weight)
#         submission_risk = max(0, (1 - metrics.submission_rate) / 0.3)  # Scaled risk
#         submission_risk = min(1, submission_risk)  # Cap at 1
#         risk_score += submission_risk * 30
#
#         # Trend component (20% weight)
#         trend_weights = {
#             "DECLINING": 1.0,
#             "STABLE": 0.3,
#             "IMPROVING": 0.0,
#             "INSUFFICIENT_DATA": 0.2,
#         }
#         risk_score += trend_weights.get(metrics.grade_trend, 0.2) * 20
#
#         # Recency component (10% weight) - recent inactivity is concerning
#         recency_risk = max(
#             0, 1 - (metrics.recency_score / 10)
#         )  # Days since last activity
#         risk_score += recency_risk * 10
#
#         # Determine risk level
#         if risk_score >= 70:
#             level = RiskLevel.HIGH_RISK
#         elif risk_score >= 50:
#             level = RiskLevel.MODERATE_RISK
#         elif risk_score >= 25:
#             level = RiskLevel.LOW_RISK
#         else:
#             level = RiskLevel.NOT_AT_RISK
#
#         return risk_score, level
#
#     @staticmethod
#     def momentum_based_method(metrics: RiskMetrics) -> Tuple[bool, str]:
#         """
#         METHOD 2: Momentum-Based Detection
#
#         Focuses on RECENT TREND rather than overall average.
#         Better for catching students in downward spiral.
#
#         Scenario:
#         - Student with 85%, 80%, 75%, 70%, 65% (DECLINING - FLAG)
#         - Student with 40%, 45%, 50%, 55%, 60% (IMPROVING - DON'T FLAG)
#
#         Benefits:
#         - Catches students early in decline
#         - Gives credit to students improving despite low average
#         - More predictive of intervention need
#         """
#         at_risk = False
#         reason = "Not at risk"
#
#         # Strong negative momentum + low grades
#         if (
#             metrics.grade_trend == "DECLINING"
#             and metrics.avg_grade is not None
#             and metrics.avg_grade < 60
#         ):
#             at_risk = True
#             reason = "Declining grades with average below 60%"
#
#         # Poor submissions + low grades
#         elif (
#             metrics.submission_rate < 0.5
#             and metrics.avg_grade is not None
#             and metrics.avg_grade < 60
#         ):
#             at_risk = True
#             reason = "Very low submission rate with low grades"
#
#         # Strong declining momentum even with decent average
#         elif (
#             metrics.grade_trend == "DECLINING"
#             and metrics.avg_grade is not None
#             and metrics.avg_grade < 70
#             and metrics.momentum < -5
#         ):  # Losing 5+ points per submission
#             at_risk = True
#             reason = "Strong downward momentum"
#
#         return at_risk, reason
#
#     @staticmethod
#     def variance_based_method(metrics: RiskMetrics) -> Tuple[bool, str]:
#         """
#         METHOD 3: Variance Detection
#
#         High variance (inconsistent performance) + low average = at-risk
#         Suggests student doesn't understand material consistently.
#
#         Scenario:
#         - Student with scores: 90, 10, 85, 15, 80 (avg=56, var=high - FLAG)
#         - Student with scores: 50, 52, 48, 51, 49 (avg=50, var=low - LESS CONCERNING)
#
#         Benefits:
#         - Catches students with inconsistent understanding
#         - Identifies concentration/focus issues
#         - Different intervention strategy than consistent low performer
#         """
#         at_risk = False
#         reason = "Not at risk"
#
#         # Low average with high variance = confusing performance
#         if (
#             metrics.avg_grade is not None
#             and metrics.avg_grade < 60
#             and metrics.grade_variance > 15
#         ):
#             at_risk = True
#             reason = "Inconsistent performance with low average (possible comprehension gaps)"
#
#         # Consistently low but stable (less concerning)
#         elif (
#             metrics.avg_grade is not None
#             and metrics.avg_grade < 50
#             and metrics.grade_variance < 10
#         ):
#             at_risk = True
#             reason = "Consistently poor performance"
#
#         return at_risk, reason
#
#     @staticmethod
#     def multi_factor_composite_method(
#         metrics: RiskMetrics,
#     ) -> Tuple[float, RiskLevel, Dict]:
#         """
#         METHOD 4: Advanced Multi-Factor Composite (RECOMMENDED)
#
#         Combines multiple methods with detailed reasoning.
#         Most comprehensive approach.
#
#         Returns:
#         - risk_score: 0-100 scale
#         - risk_level: RiskLevel enum
#         - details: Dictionary with reasoning for each factor
#         """
#         details = {"factors": {}, "triggered_flags": [], "recommendations": []}
#
#         risk_score = 0
#
#         # FACTOR 1: Grade Performance (40 points possible)
#         if metrics.avg_grade is not None:
#             if metrics.avg_grade < 40:
#                 points = 40
#                 details["triggered_flags"].append("Critical: Grade below 40%")
#                 details["recommendations"].append(
#                     "Immediate 1-on-1 intervention needed"
#                 )
#             elif metrics.avg_grade < 50:
#                 points = 30
#                 details["triggered_flags"].append("Poor: Grade below 50%")
#                 details["recommendations"].append("Tutoring or support recommended")
#             elif metrics.avg_grade < 60:
#                 points = 15
#                 details["triggered_flags"].append("Below target: Grade below 60%")
#             else:
#                 points = 0
#
#             risk_score += points
#             details["factors"]["grade"] = {"score": metrics.avg_grade, "points": points}
#
#         # FACTOR 2: Submission Consistency (25 points possible)
#         if metrics.submission_rate < 0.5:
#             points = 25
#             details["triggered_flags"].append("Critical: Submission rate below 50%")
#             details["recommendations"].append("Check for attendance/engagement issues")
#         elif metrics.submission_rate < 0.7:
#             points = 15
#             details["triggered_flags"].append("Low submission rate")
#         else:
#             points = 0
#
#         risk_score += points
#         details["factors"]["submission"] = {
#             "rate": metrics.submission_rate,
#             "points": points,
#         }
#
#         # FACTOR 3: Trend Direction (20 points possible)
#         trend_points = {
#             "DECLINING": 20,
#             "STABLE": 5,
#             "IMPROVING": -10,  # Reduces risk
#             "INSUFFICIENT_DATA": 0,
#         }
#         points = trend_points.get(metrics.grade_trend, 0)
#         if points > 0:
#             details["triggered_flags"].append(f"Negative trend: {metrics.grade_trend}")
#
#         risk_score += points
#         details["factors"]["trend"] = {"trend": metrics.grade_trend, "points": points}
#
#         # FACTOR 4: Performance Volatility (10 points possible)
#         if metrics.grade_variance > 20:
#             points = 10
#             details["triggered_flags"].append("High variance in performance")
#             details["recommendations"].append(
#                 "Investigate comprehension gaps or focus issues"
#             )
#         elif metrics.grade_variance > 15:
#             points = 5
#         else:
#             points = 0
#
#         risk_score += points
#         details["factors"]["variance"] = {
#             "variance": metrics.grade_variance,
#             "points": points,
#         }
#
#         # FACTOR 5: Recency of Activity (5 points possible)
#         # Inactive students are concerning
#         if metrics.recency_score > 21:  # 3+ weeks inactive
#             points = 5
#             details["triggered_flags"].append("Student inactive for 3+ weeks")
#             details["recommendations"].append("Check student status and engagement")
#         else:
#             points = 0
#
#         risk_score += points
#         details["factors"]["recency"] = {
#             "days_inactive": metrics.recency_score,
#             "points": points,
#         }
#
#         # Determine overall risk level
#         if risk_score >= 75:
#             level = RiskLevel.HIGH_RISK
#         elif risk_score >= 50:
#             level = RiskLevel.MODERATE_RISK
#         elif risk_score >= 25:
#             level = RiskLevel.LOW_RISK
#         else:
#             level = RiskLevel.NOT_AT_RISK
#
#         details["total_risk_score"] = risk_score
#         details["risk_level"] = level.name
#
#         return risk_score, level, details
#
#
# # ============================================================================
# # IMPLEMENTATION EXAMPLES
# # ============================================================================
#
#
# def example_comparison():
#     """
#     Example showing how different methods would flag the same student differently
#     """
#
#     # Student A: Consistently poor
#     metrics_a = RiskMetrics(
#         avg_grade=45,
#         submission_rate=0.65,
#         grade_trend="STABLE",
#         grade_variance=5,
#         momentum=0,
#         weeks_active=8,
#         recency_score=3,
#     )
#
#     # Student B: Declining but recovering
#     metrics_b = RiskMetrics(
#         avg_grade=62,
#         submission_rate=0.8,
#         grade_trend="IMPROVING",
#         grade_variance=18,
#         momentum=3,  # Positive momentum
#         weeks_active=6,
#         recency_score=2,
#     )
#
#     # Student C: Crashing down
#     metrics_c = RiskMetrics(
#         avg_grade=55,
#         submission_rate=0.6,
#         grade_trend="DECLINING",
#         grade_variance=25,
#         momentum=-8,  # Steep decline
#         weeks_active=4,
#         recency_score=14,  # Inactive recently
#     )
#
#     calc = AtRiskCalculator
#
#     print("=" * 70)
#     print("STUDENT A: Consistently Poor")
#     print("=" * 70)
#     print(f"Simple Method (current): {calc.simple_threshold_method(metrics_a)}")
#     score, level = calc.weighted_score_method(metrics_a)
#     print(f"Weighted Score Method: {score:.1f} ({level.name})")
#     at_risk, reason = calc.momentum_based_method(metrics_a)
#     print(f"Momentum Method: {at_risk} - {reason}")
#     score, level, details = calc.multi_factor_composite_method(metrics_a)
#     print(f"Multi-Factor Method: {score:.1f} ({level.name})")
#     print(f"  Flags: {', '.join(details['triggered_flags'])}")
#
#     print("\n" + "=" * 70)
#     print("STUDENT B: Improving (False Positive in Simple Method)")
#     print("=" * 70)
#     print(f"Simple Method (current): {calc.simple_threshold_method(metrics_b)}")
#     score, level = calc.weighted_score_method(metrics_b)
#     print(f"Weighted Score Method: {score:.1f} ({level.name})")
#     at_risk, reason = calc.momentum_based_method(metrics_b)
#     print(f"Momentum Method: {at_risk} - {reason}")
#     score, level, details = calc.multi_factor_composite_method(metrics_b)
#     print(f"Multi-Factor Method: {score:.1f} ({level.name})")
#     print(
#         f"  Flags: {', '.join(details['triggered_flags']) if details['triggered_flags'] else 'None'}"
#     )
#
#     print("\n" + "=" * 70)
#     print("STUDENT C: Rapidly Declining (Urgent Need)")
#     print("=" * 70)
#     print(f"Simple Method (current): {calc.simple_threshold_method(metrics_c)}")
#     score, level = calc.weighted_score_method(metrics_c)
#     print(f"Weighted Score Method: {score:.1f} ({level.name})")
#     at_risk, reason = calc.momentum_based_method(metrics_c)
#     print(f"Momentum Method: {at_risk} - {reason}")
#     score, level, details = calc.multi_factor_composite_method(metrics_c)
#     print(f"Multi-Factor Method: {score:.1f} ({level.name})")
#     print(f"  Flags: {', '.join(details['triggered_flags'])}")
#     print(f"  Recommendations: {', '.join(details['recommendations'])}")
#
#
# if __name__ == "__main__":
#     example_comparison()
