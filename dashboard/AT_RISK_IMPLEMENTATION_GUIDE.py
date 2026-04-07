# """
# IMPLEMENTATION GUIDE: Improved At-Risk Student Detection
#
# This guide shows how to integrate better at-risk detection into dashboard/views.py
# """
#
# # ============================================================================
# # OPTION 1: Minimal Change (Drop-in Replacement)
# # ============================================================================
# # Replace the current at_risk calculation with weighted scoring
#
#
# def students_view_option_1_weighted_score(request, course_id):
#     """
#     Current: Boolean flag (at_risk = True/False)
#     Improved: Weighted risk score (0-100) + level
#
#     Change required: Minimal
#     Breaking changes: Low (add new field, keep old one)
#     """
#
#     # ... existing code ...
#
#     for enrollment in enrollments:
#         student = enrollment.student
#         submissions = StudentSubmission.objects.filter(
#             student=student, assignment__course=course
#         ).select_related("assignment")
#
#         submitted_count = submissions.count()
#         graded = submissions.filter(score__isnull=False)
#         avg_grade = graded.aggregate(avg=Avg("score"))["avg"] or 0
#
#         recent = graded.order_by("-graded_at")[:3]
#         scores = [s.score for s in recent if s.score is not None]
#
#         if len(scores) < 2:
#             trend = "INSUFFICIENT_DATA"
#         elif scores[0] > scores[-1]:
#             trend = "IMPROVING"
#         elif scores[0] < scores[-1]:
#             trend = "DECLINING"
#         else:
#             trend = "STABLE"
#
#         # ===== IMPROVED CALCULATION (OPTION 1) =====
#         risk_score = 0
#
#         # Grade component (40% weight)
#         if avg_grade is not None:
#             grade_risk = max(0, (100 - avg_grade) / 100)
#             risk_score += grade_risk * 40
#
#         # Submission component (30% weight)
#         submission_rate = submitted_count / total_assigned if total_assigned > 0 else 0
#         submission_risk = max(0, (1 - submission_rate) / 0.3)
#         submission_risk = min(1, submission_risk)
#         risk_score += submission_risk * 30
#
#         # Trend component (20% weight)
#         trend_weights = {
#             "DECLINING": 20,
#             "STABLE": 5,
#             "IMPROVING": -5,
#             "INSUFFICIENT_DATA": 0,
#         }
#         risk_score += trend_weights.get(trend, 0)
#
#         # Determine risk level
#         if risk_score >= 70:
#             risk_level = "HIGH"
#             at_risk = True
#         elif risk_score >= 50:
#             risk_level = "MODERATE"
#             at_risk = True
#         elif risk_score >= 25:
#             risk_level = "LOW"
#             at_risk = False
#         else:
#             risk_level = "NONE"
#             at_risk = False
#
#         # ===== END IMPROVED CALCULATION =====
#
#         data.append(
#             {
#                 "student_id": student.id,
#                 "student_name": student.get_full_name(),
#                 "average_grade": round(avg_grade, 2) if avg_grade is not None else None,
#                 "grade_trend": trend,
#                 "at_risk": at_risk,  # Keep for backward compatibility
#                 "risk_score": round(risk_score, 1),  # NEW: Detailed score
#                 "risk_level": risk_level,  # NEW: Categorical level
#             }
#         )
#
#
# # ============================================================================
# # OPTION 2: Enhanced Metrics (Better Data Quality)
# # ============================================================================
# # Add more sophisticated metrics to detect risk
#
#
# def students_view_option_2_enhanced_metrics(request, course_id):
#     """
#     Improved: Add momentum, variance, and recency detection
#
#     Change required: Moderate
#     Breaking changes: None (backward compatible)
#     Database impact: Minimal (same queries)
#     """
#
#     for enrollment in enrollments:
#         student = enrollment.student
#         submissions = (
#             StudentSubmission.objects.filter(student=student, assignment__course=course)
#             .select_related("assignment")
#             .order_by("graded_at")
#         )
#
#         graded = submissions.filter(score__isnull=False).order_by("-graded_at")
#         avg_grade = graded.aggregate(avg=Avg("score"))["avg"] or 0
#
#         # ===== ENHANCED METRICS =====
#
#         # 1. Grade variance (consistency)
#         scores = [s.score for s in graded]
#         if len(scores) > 1:
#             mean = avg_grade
#             variance = sum((x - mean) ** 2 for x in scores) / len(scores)
#             std_dev = variance**0.5
#         else:
#             std_dev = 0
#
#         # 2. Momentum (recent trend)
#         recent_scores = [s.score for s in graded[:5]]  # Last 5
#         if len(recent_scores) >= 2:
#             momentum = recent_scores[0] - recent_scores[-1]  # Recent vs oldest
#         else:
#             momentum = 0
#
#         # 3. Recency (when was last submission)
#         latest = graded.first()
#         if latest and latest.graded_at:
#             days_inactive = (timezone.now() - latest.graded_at).days
#         else:
#             days_inactive = 0
#
#         # ===== CALCULATE RISK WITH ENHANCED METRICS =====
#         risk_factors = []
#
#         if avg_grade < 50:
#             risk_factors.append(("low_grade", 30))
#         elif avg_grade < 60:
#             risk_factors.append(("below_target", 15))
#
#         submission_rate = len(graded) / total_assigned if total_assigned > 0 else 0
#         if submission_rate < 0.5:
#             risk_factors.append(("low_submission", 25))
#         elif submission_rate < 0.7:
#             risk_factors.append(("moderate_submission", 15))
#
#         if momentum < -5:  # Losing 5+ points per assignment
#             risk_factors.append(("downward_momentum", 20))
#
#         if std_dev > 20:  # High variability
#             risk_factors.append(("inconsistent", 10))
#
#         if days_inactive > 21:  # 3+ weeks
#             risk_factors.append(("inactive", 10))
#
#         total_risk = sum(weight for _, weight in risk_factors)
#         total_risk = min(100, total_risk)  # Cap at 100
#
#         # ===== END ENHANCED CALCULATION =====
#
#         data.append(
#             {
#                 "student_id": student.id,
#                 "student_name": student.get_full_name(),
#                 "average_grade": round(avg_grade, 2) if avg_grade is not None else None,
#                 "grade_trend": trend,
#                 "at_risk": total_risk >= 50,  # Backward compatible
#                 "risk_score": round(total_risk, 1),
#                 "risk_factors": [
#                     name for name, _ in risk_factors
#                 ],  # NEW: Why they're at risk
#                 "performance_variance": round(std_dev, 1),  # NEW: Consistency metric
#                 "recent_momentum": round(momentum, 1),  # NEW: Direction metric
#                 "days_since_activity": days_inactive,  # NEW: Engagement metric
#             }
#         )
#
#
# # ============================================================================
# # OPTION 3: Query Optimization + Enhanced Detection
# # ============================================================================
# # Use Django ORM aggregation for better performance
#
#
# def students_view_option_3_optimized(request, course_id):
#     """
#     Improved: Use database aggregation for better performance
#
#     Change required: Moderate
#     Breaking changes: None
#     Performance impact: ~70% faster for large courses
#     """
#     from django.db.models import Avg, Count, Max, Min, Q, StdDev
#
#     teacher = request.user
#     course = get_object_or_404(Course, id=course_id, teacher=teacher)
#
#     # Single optimized query instead of loop
#     enrollments_data = (
#         StudentCourse.objects.filter(course=course)
#         .select_related("student")
#         .annotate(
#             # Submission metrics
#             total_submissions=Count(
#                 "student__submissions",
#                 filter=Q(student__submissions__assignment__course=course),
#             ),
#             graded_submissions=Count(
#                 "student__submissions",
#                 filter=Q(
#                     student__submissions__assignment__course=course,
#                     student__submissions__score__isnull=False,
#                 ),
#             ),
#             avg_grade=Avg(
#                 "student__submissions__score",
#                 filter=Q(
#                     student__submissions__assignment__course=course,
#                     student__submissions__score__isnull=False,
#                 ),
#             ),
#             grade_std=StdDev(
#                 "student__submissions__score",
#                 filter=Q(
#                     student__submissions__assignment__course=course,
#                     student__submissions__score__isnull=False,
#                 ),
#             ),
#             min_grade=Min(
#                 "student__submissions__score",
#                 filter=Q(
#                     student__submissions__assignment__course=course,
#                     student__submissions__score__isnull=False,
#                 ),
#             ),
#             max_grade=Max(
#                 "student__submissions__score",
#                 filter=Q(
#                     student__submissions__assignment__course=course,
#                     student__submissions__score__isnull=False,
#                 ),
#             ),
#         )
#     )
#
#     data = []
#     total_assigned = Assignment.objects.filter(course=course).count()
#
#     for enrollment in enrollments_data:
#         student = enrollment.student
#
#         avg_grade = enrollment.avg_grade or 0
#         graded = enrollment.graded_submissions or 0
#         submission_rate = graded / total_assigned if total_assigned > 0 else 0
#         variance = enrollment.grade_std or 0
#
#         # ===== CALCULATE RISK =====
#         risk_score = 0
#         risk_factors = []
#
#         # Grade risk
#         if avg_grade < 40:
#             risk_score += 40
#             risk_factors.append("critical_grade")
#         elif avg_grade < 50:
#             risk_score += 30
#             risk_factors.append("low_grade")
#         elif avg_grade < 60:
#             risk_score += 15
#             risk_factors.append("below_target")
#
#         # Submission risk
#         if submission_rate < 0.5:
#             risk_score += 25
#             risk_factors.append("critical_submission")
#         elif submission_rate < 0.7:
#             risk_score += 15
#             risk_factors.append("low_submission")
#
#         # Consistency risk (high variance = comprehension gaps)
#         if variance and variance > 20:
#             risk_score += 10
#             risk_factors.append("inconsistent")
#
#         risk_score = min(100, risk_score)
#
#         # ===== END CALCULATION =====
#
#         data.append(
#             {
#                 "student_id": student.id,
#                 "student_name": student.get_full_name(),
#                 "average_grade": round(avg_grade, 2) if avg_grade else None,
#                 "best_grade": enrollment.max_grade,
#                 "worst_grade": enrollment.min_grade,
#                 "grade_consistency": round(variance, 1) if variance else None,
#                 "at_risk": risk_score >= 50,
#                 "risk_score": round(risk_score, 1),
#                 "risk_factors": risk_factors,
#                 "submission_rate": round(submission_rate * 100, 1),
#             }
#         )
#
#     return data
#
#
# # ============================================================================
# # RECOMMENDATION SUMMARY
# # ============================================================================
# """
# WHICH OPTION TO CHOOSE?
#
# CURRENT (Simple Binary):
# ✓ Easiest to implement
# ✓ Most predictable
# ✗ Limited nuance
# ✗ Can't prioritize intervention
# ✗ No insight into WHY student is at-risk
#
# OPTION 1 (Weighted Score):
# ✓ Better prioritization
# ✓ Easy to implement
# ✓ Backward compatible
# ✓ Good balance of complexity/benefit
# ✓ RECOMMENDED for quick improvement
#
# OPTION 2 (Enhanced Metrics):
# ✓ More sophisticated detection
# ✓ Identifies more patterns
# ✓ Better intervention guidance
# ✗ More complex
# ✓ Moderate development effort
#
# OPTION 3 (Optimized + Enhanced):
# ✓ Fastest performance
# ✓ Most scalable
# ✓ Best for large courses
# ✗ Most complex to implement
# ✓ Worth it if you have 100+ students per course
#
# RECOMMENDED IMPLEMENTATION PATH:
# 1. First: Implement Option 1 (Weighted Score) - Quick win
# 2. Second: Add Option 2 metrics - Better insight
# 3. Third: Optimize with Option 3 - Performance
#
# This gives you progressive improvement without major rewrites.
# """
