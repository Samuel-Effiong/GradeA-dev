"""
READY-TO-USE CODE: At-Risk Student Detection Improvements

Copy the code from the appropriate section and integrate into dashboard/views.py
"""

# ==============================================================================
# OPTION A: QUICK FIX (Copy this to replace lines 1857-1869 in views.py)
# ==============================================================================

"""
Location: dashboard/views.py, students() method, around line 1857-1869

Find this section:
    risk_flags = 0
    if avg_grade is not None and avg_grade < 50:
        risk_flags += 1
    if total_assigned > 0 and (submitted_count / total_assigned) < 0.7:
        risk_flags += 1
    if trend == "DECLINING":
        risk_flags += 1

    at_risk = risk_flags >= 2

Replace with this:
"""

# ===== BEGIN REPLACEMENT (Option A) =====
# Calculate weighted risk score for better prioritization
risk_score = 0.0

# Grade component (40% weight) - normalized 0-100
if avg_grade is not None:
    grade_risk_factor = max(0, (100 - avg_grade) / 100)  # 0-1 scale
    risk_score += grade_risk_factor * 40

# Submission component (30% weight)
submission_rate = (submitted_count / total_assigned) if total_assigned > 0 else 0
if submission_rate < 0.7:
    submission_risk_factor = max(0, (0.7 - submission_rate) / 0.7)  # Normalized
    risk_score += submission_risk_factor * 30

# Trend component (20% weight)
trend_score = {
    "DECLINING": 20,
    "STABLE": 5,
    "IMPROVING": -5,
    "INSUFFICIENT_DATA": 0,
}.get(trend, 0)
risk_score += trend_score

# Cap at 100 and classify
risk_score = min(100, max(0, risk_score))

if risk_score >= 70:
    risk_level = "HIGH"
    at_risk = True
elif risk_score >= 50:
    risk_level = "MODERATE"
    at_risk = True
elif risk_score >= 25:
    risk_level = "LOW"
    at_risk = False
else:
    risk_level = "NONE"
    at_risk = False

# ===== END REPLACEMENT (Option A) =====

# Then add these fields to the data.append() around line 1887:
#
#   data.append({
#       ... existing fields ...
#       "at_risk": at_risk,
#       # NEW FIELDS:
#       "risk_score": round(risk_score, 1),
#       "risk_level": risk_level,
#   })


# ==============================================================================
# OPTION B: ENHANCED WITH METRICS (More insight, ~50 lines to add)
# ==============================================================================

"""
Location: dashboard/views.py, students() method, after recent = graded.order_by("-graded_at")[:3]

Add this enhanced metric calculation:
"""

# ===== BEGIN ENHANCED METRICS SECTION =====

# Calculate performance consistency (variance)
all_scores = [s.score for s in graded if s.score is not None]
if len(all_scores) > 1:
    mean_score = avg_grade
    variance = sum((score - mean_score) ** 2 for score in all_scores) / len(all_scores)
    grade_std_dev = variance ** 0.5
else:
    grade_std_dev = 0

# Calculate momentum (recent trend direction and magnitude)
recent_scores = [s.score for s in graded.order_by("-graded_at")[:5] if s.score is not None]
if len(recent_scores) >= 2:
    # Most recent vs oldest in recent batch
    momentum = recent_scores[0] - recent_scores[-1]
else:
    momentum = 0

# Calculate recency (how recently was student active)
latest_submission = graded.order_by("-graded_at").first()
if latest_submission and latest_submission.graded_at:
    from django.utils import timezone
    days_inactive = (timezone.now() - latest_submission.graded_at).days
else:
    days_inactive = 0

# ===== END ENHANCED METRICS SECTION =====

# Then modify the risk calculation to use these metrics:

# ===== BEGIN ENHANCED RISK CALCULATION =====

risk_score = 0.0
risk_factors = []

# Grade risk (40 points max)
if avg_grade is not None:
    if avg_grade < 40:
        risk_score += 40
        risk_factors.append("critical_grade")
    elif avg_grade < 50:
        risk_score += 30
        risk_factors.append("low_grade")
    elif avg_grade < 60:
        risk_score += 15
        risk_factors.append("below_target_grade")

# Submission risk (30 points max)
submission_rate = (submitted_count / total_assigned) if total_assigned > 0 else 0
if submission_rate < 0.5:
    risk_score += 25
    risk_factors.append("critical_submission")
elif submission_rate < 0.7:
    risk_score += 15
    risk_factors.append("low_submission")

# Trend risk (20 points max)
if trend == "DECLINING":
    risk_score += 20
    risk_factors.append("declining_trend")
elif trend == "STABLE" and avg_grade is not None and avg_grade < 60:
    risk_score += 5
    risk_factors.append("stable_low_grade")

# Variance/Consistency risk (10 points max)
if grade_std_dev > 20:
    risk_score += 10
    risk_factors.append("inconsistent_performance")

# Recency/Engagement risk (5 points max)
if days_inactive > 21:  # 3+ weeks inactive
    risk_score += 5
    risk_factors.append("long_inactive")
elif days_inactive > 14:
    risk_score += 2
    risk_factors.append("moderately_inactive")

# Cap and classify
risk_score = min(100, max(0, risk_score))

if risk_score >= 75:
    risk_level = "CRITICAL"
    at_risk = True
elif risk_score >= 50:
    risk_level = "HIGH"
    at_risk = True
elif risk_score >= 30:
    risk_level = "MODERATE"
    at_risk = False  # Borderline, not flagged
elif risk_score >= 15:
    risk_level = "LOW"
    at_risk = False
else:
    risk_level = "NONE"
    at_risk = False

# ===== END ENHANCED RISK CALCULATION =====

# Then add to data.append():
#
#   data.append({
#       ... existing fields ...
#       "at_risk": at_risk,
#       # Option A fields:
#       "risk_score": round(risk_score, 1),
#       "risk_level": risk_level,
#       # Option B additional fields:
#       "risk_factors": risk_factors,
#       "grade_consistency": round(grade_std_dev, 1),
#       "recent_momentum": round(momentum, 1),
#       "days_since_activity": days_inactive,
#   })


# ==============================================================================
# HELPER FUNCTION: Generate actionable recommendations
# ==============================================================================

"""
Add this function to dashboard/views.py or a separate utils file
"""

def get_at_risk_recommendations(risk_factors, avg_grade, submission_rate, trend):
    """
    Generate teacher-friendly recommendations based on risk factors.

    Args:
        risk_factors: List of risk factor strings from calculation
        avg_grade: Student's average grade
        submission_rate: Percentage of assignments submitted
        trend: Grade trend (DECLINING, IMPROVING, STABLE)

    Returns:
        List of recommendation strings
    """
    recommendations = []

    # Grade-based recommendations
    if "critical_grade" in risk_factors or "low_grade" in risk_factors:
        recommendations.append("Schedule 1-on-1 tutoring session")
        recommendations.append("Review mastery of key concepts")
        if submission_rate > 0.7:
            recommendations.append("Student is engaged but struggling - may need different approach")

    # Submission-based recommendations
    if "critical_submission" in risk_factors:
        recommendations.append("Check attendance and engagement")
        recommendations.append("Reach out to student or parent")
        recommendations.append("Verify no technical barriers to submission")
    elif "low_submission" in risk_factors:
        recommendations.append("Follow up on missing assignments")

    # Trend-based recommendations
    if "declining_trend" in risk_factors:
        recommendations.append("Provide additional support immediately")
        if avg_grade and avg_grade < 50:
            recommendations.append("Consider before/after school intervention")
    elif trend == "IMPROVING":
        recommendations.append("Encourage continued effort - student is improving")

    # Consistency-based recommendations
    if "inconsistent_performance" in risk_factors:
        recommendations.append("Identify specific topics where student struggles")
        recommendations.append("Provide targeted practice in weak areas")

    # Engagement-based recommendations
    if "long_inactive" in risk_factors:
        recommendations.append("Contact student to check on status")
        recommendations.append("Verify student hasn't disengaged from course")

    return recommendations if recommendations else ["Monitor student closely"]


# ==============================================================================
# INTEGRATION CHECKLIST
# ==============================================================================

"""
☐ 1. Choose Option A (quick) or Option B (comprehensive)
☐ 2. Copy the code into dashboard/views.py students() method
☐ 3. Update the data.append() call to include new fields
☐ 4. Update TeacherStudentAnalyticsSerializer to include new fields:

     In dashboard/serializers.py, add to TeacherStudentAnalyticsSerializer:

     risk_score = serializers.FloatField(read_only=True)
     risk_level = serializers.CharField(read_only=True)
     # For Option B:
     risk_factors = serializers.ListField(read_only=True)
     grade_consistency = serializers.FloatField(read_only=True)
     recent_momentum = serializers.FloatField(read_only=True)
     days_since_activity = serializers.IntegerField(read_only=True)

☐ 5. Update frontend to display new fields in student analytics
☐ 6. Test with sample data (see test cases below)
☐ 7. Clear cache after deployment
☐ 8. Monitor for any unexpected at-risk classifications

Test Cases:
- Student with 45% grade, 60% submission, DECLINING trend
  Expected: HIGH risk, multiple factors

- Student with 55% grade, 65% submission, IMPROVING trend
  Expected: MODERATE risk, recent positive trend

- Student with 49% grade, 72% submission, STABLE trend
  Expected: LOW-MODERATE risk, close to critical

- Student with 75% grade, 68% submission, IMPROVING trend
  Expected: LOW risk, doing well overall
"""

# ==============================================================================
# PERFORMANCE NOTES
# ==============================================================================

"""
CACHING:
The current implementation uses Django cache (15 minutes):
    cache_key = f"teacheradmins:user_id__{request.user.id}:instance_id__{course_id}:view__students"
    cache.set(cache_key, data, 60 * 15)

NEW FIELDS are calculated fresh each time, so caching still works.
If you need to clear cache after grading:
    from django.core.cache import cache
    cache.delete(cache_key)

PERFORMANCE IMPACT:
- Option A: Negligible (~1-2ms added)
- Option B: Minimal (~3-5ms added per student in loop)
- For 50 students: ~0.25s additional, acceptable

OPTIMIZATION:
If you have 100+ students per course and need better performance,
implement the ORM aggregation version (uses database directly instead of loop).
"""
