from django.contrib.humanize.templatetags.humanize import naturaltime
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from ai_processor.models import ChatMessage, ChatSession
from assignments.models import Assignment
from classrooms.models import Course

# from assignments.models import Assignment


class PeakTimeSerializer(serializers.Serializer):
    hour = serializers.IntegerField()
    label = serializers.CharField()
    average_users = serializers.FloatField()


class ConcurrencySerializer(serializers.Serializer):
    time_range = serializers.CharField()
    start = serializers.DateTimeField()
    end = serializers.DateTimeField()
    peak_concurrent_users = serializers.IntegerField()
    peak_time_of_day = PeakTimeSerializer(allow_null=True, required=False)


class StudentAssignmentListSerializer(serializers.Serializer):
    course = serializers.CharField()
    teacher = serializers.CharField()
    assignment_id = serializers.UUIDField()
    title = serializers.CharField()
    due_date = serializers.DateTimeField()
    submission_date = serializers.DateTimeField()
    score = serializers.IntegerField()
    score_percentage = serializers.FloatField(read_only=True)
    total_score = serializers.IntegerField()
    feedback = serializers.CharField()
    submission_status = serializers.CharField(max_length=30)

    # class Meta:
    #     model = Assignment
    #     fields = [
    #         "course",
    #         "teacher",
    #         "assignment",
    #         "title",
    #         "due_date",
    #         "submission_date",
    #         "score",
    #         "score_percentage",
    #         "total_score",
    #         "feedback",
    #         "submission_status",
    #     ]


class AssignmentPerformanceSerializer(serializers.Serializer):
    """Serializer for best/worst assignment entries in the student dashboard"""

    assignment_id = serializers.UUIDField(source="assignment.id", read_only=True)
    assignment_name = serializers.CharField(source="assignment.title", read_only=True)
    score = serializers.FloatField(read_only=True)
    score_percentage = serializers.FloatField(read_only=True)


class StudentCourseGradeSerializer(serializers.Serializer):
    """Serializer for a student's performance metrics inside a single course"""

    course_id = serializers.UUIDField(read_only=True)
    course_name = serializers.CharField(read_only=True)
    score = serializers.FloatField(read_only=True)
    percentage = serializers.FloatField(read_only=True)
    grade = serializers.CharField(read_only=True)
    gpa = serializers.FloatField(read_only=True)


class StudentDashboardOverviewSerializer(serializers.Serializer):
    """Serializer for the student dashboard overview metrics"""

    total_courses = serializers.IntegerField(read_only=True)
    assignments_submitted = serializers.IntegerField(read_only=True)
    assignments_pending_not_due = serializers.IntegerField(read_only=True)
    assignments_due_no_submission = serializers.IntegerField(read_only=True)

    # Grade Standing Metrics
    overall_percentage = serializers.FloatField(read_only=True)
    overall_grade = serializers.CharField(read_only=True)
    overall_gpa = serializers.FloatField(read_only=True)
    overall_remark = serializers.CharField(read_only=True)

    # Per-Course Breakdowns
    courses_grades = StudentCourseGradeSerializer(many=True, read_only=True)


class CourseAnalyticsSerializer(serializers.Serializer):
    """Main serializer for the student course analytics dashboard"""

    course = serializers.UUIDField(read_only=True)
    assignment_submitted = serializers.IntegerField(read_only=True)
    assignment_assigned = serializers.IntegerField(read_only=True)
    completion_rate = serializers.FloatField(read_only=True)
    missing_or_overdue = serializers.IntegerField(read_only=True)
    average_grade = serializers.FloatField(read_only=True)
    grade_trend = serializers.CharField(read_only=True)

    best_assignments = AssignmentPerformanceSerializer(many=True, read_only=True)
    worst_assignments = AssignmentPerformanceSerializer(many=True, read_only=True)


class GradeDistributionEntrySerializer(serializers.Serializer):
    """Serializer for a single grade tier with count and percentage"""

    count = serializers.IntegerField(read_only=True)
    percentage = serializers.FloatField(read_only=True)


class GradeDistributionSerializer(serializers.Serializer):
    """Serializer for A-F grade distribution with dual-format entries"""

    A = GradeDistributionEntrySerializer(read_only=True)
    B = GradeDistributionEntrySerializer(read_only=True)
    C = GradeDistributionEntrySerializer(read_only=True)
    D = GradeDistributionEntrySerializer(read_only=True)
    E = GradeDistributionEntrySerializer(read_only=True)
    F = GradeDistributionEntrySerializer(read_only=True)


class UpcomingAssignmentSerializer(serializers.Serializer):
    """Serializer for upcoming assignments in the teacher dashboard"""

    id = serializers.UUIDField(read_only=True)
    title = serializers.CharField(read_only=True)
    course_id = serializers.UUIDField(source="course.id", read_only=True)
    course_name = serializers.CharField(source="course.name", read_only=True)
    remaining_time = serializers.SerializerMethodField()
    exact_due_date = serializers.SerializerMethodField()

    def get_remaining_time(self, obj):
        if obj.due_date:
            return naturaltime(obj.due_date)
        return "N/A"

    def get_exact_due_date(self, obj):
        if obj.due_date:
            return obj.due_date.strftime("%b %d, %Y, %I:%M %p")
        return "N/A"


class CoursePerformanceSerializer(serializers.Serializer):
    """Serializer for a single course's performance metrics"""

    id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(read_only=True)
    average_grade = serializers.FloatField(read_only=True)
    total_submissions = serializers.FloatField(default=0, required=False)
    submission_rate = serializers.FloatField(read_only=True)


class AtRiskStudentSerializer(serializers.Serializer):
    """Serializer for a student flagged as at-risk in a specific course"""

    student_id = serializers.UUIDField(read_only=True)
    student_name = serializers.CharField(read_only=True)
    course_id = serializers.UUIDField(read_only=True)
    course_name = serializers.CharField(read_only=True)
    average_grade = serializers.FloatField(read_only=True, allow_null=True)
    grade_trend = serializers.CharField(read_only=True)


class AITrustStatsSerializer(serializers.Serializer):
    """Serializer for AI trust and confidence metrics"""

    average_ai_extraction_confidence = serializers.FloatField(read_only=True)
    average_ai_grading_confidence = serializers.FloatField(read_only=True)
    low_confidence_rate = serializers.FloatField(read_only=True)


class TeacherDashboardOverviewSerializer(serializers.Serializer):
    """Serializer for the teacher dashboard overview metrics"""

    total_assignments_assigned = serializers.IntegerField(read_only=True)
    total_assignments_graded = serializers.IntegerField(read_only=True)
    total_assignment_pending_grade = serializers.IntegerField(read_only=True)
    total_students = serializers.IntegerField(read_only=True)
    percentage_graded = serializers.FloatField(read_only=True)
    grade_distribution = GradeDistributionSerializer(read_only=True)
    course_performance = CoursePerformanceSerializer(many=True, read_only=True)
    upcoming_assignments = UpcomingAssignmentSerializer(many=True, read_only=True)
    at_risk_students = AtRiskStudentSerializer(many=True, read_only=True)
    average_grading_turnaround = serializers.SerializerMethodField()
    ai_trust = AITrustStatsSerializer(read_only=True)

    def get_average_grading_turnaround(self, obj):
        duration = obj.get("average_grading_turnaround")
        if duration is None:
            return "N/A"

        days = duration.days
        seconds = duration.seconds
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60

        parts = []
        if days > 0:
            parts.append(f"{days}d")
        if hours > 0:
            parts.append(f"{hours}h")
        if minutes > 0:
            parts.append(f"{minutes}m")

        if not parts:
            return "< 1m"

        return " ".join(parts)


class WorkflowStatsSerializer(serializers.Serializer):
    """Serializer for assignment workflow statistics"""

    total_assignments_assigned = serializers.IntegerField(read_only=True)
    total_assignments_submitted = serializers.IntegerField(read_only=True)
    total_assignments_graded = serializers.IntegerField(read_only=True)


class AssignmentGradeByTypeSerializer(serializers.Serializer):
    """Serializer for average grade per assignment type"""

    assignment__assignment_type = serializers.CharField(read_only=True)
    avg_score = serializers.FloatField(read_only=True)


class AssignmentGradeByTopicSerializer(serializers.Serializer):
    """Serializer for average grade per assignment topic"""

    assignment__topic__name = serializers.CharField(read_only=True)
    avg_score = serializers.FloatField(read_only=True)


class LowestMasteryAssignmentSerializer(serializers.Serializer):
    """Serializer for the assignment with the lowest student mastery"""

    assignment__id = serializers.UUIDField(read_only=True)
    assignment__title = serializers.CharField(read_only=True)
    avg_score = serializers.FloatField(read_only=True)


class PerformanceStatsSerializer(serializers.Serializer):
    """Serializer for course performance metrics"""

    average_assignment_grade_by_type = AssignmentGradeByTypeSerializer(
        many=True, read_only=True
    )
    average_assignment_grade_by_topic = AssignmentGradeByTopicSerializer(
        many=True, read_only=True
    )
    lowest_mastery_assignment = LowestMasteryAssignmentSerializer(
        read_only=True, allow_null=True
    )
    course_performance_trend = serializers.CharField(read_only=True)


class TeacherCourseAnalyticsSerializer(serializers.Serializer):
    """Main serializer for the teacher course analytics dashboard"""

    workflow = WorkflowStatsSerializer(read_only=True)
    performance = PerformanceStatsSerializer(read_only=True)
    ai_trust = AITrustStatsSerializer(read_only=True)


class TeacherAssignmentAnalyticsSerializer(serializers.Serializer):
    """Serializer for the teacher assignment analytics dashboard"""

    assignment = serializers.UUIDField(read_only=True)
    title = serializers.CharField(read_only=True)
    due_date = serializers.DateTimeField(read_only=True)
    assignment_type = serializers.CharField(read_only=True)
    total_submissions = serializers.IntegerField(read_only=True)
    unit = serializers.CharField(read_only=True, allow_null=True)
    average_grade = serializers.FloatField(read_only=True)
    ai_extraction_confidence = serializers.FloatField(read_only=True)
    ai_grading_confidence = serializers.FloatField(read_only=True)


class StudentAssignmentHistorySerializer(serializers.Serializer):
    """Serializer for a student's submission history for a specific course"""

    assignment_id = serializers.UUIDField(read_only=True)
    assignment_title = serializers.CharField(read_only=True)
    submitted = serializers.BooleanField(read_only=True)
    score = serializers.FloatField(read_only=True, allow_null=True)
    score_percentage = serializers.FloatField(read_only=True, allow_null=True)
    graded_at = serializers.DateTimeField(read_only=True, allow_null=True)


class MiniAssignmentSerializer(serializers.Serializer):
    """Minimal assignment serializer for dashboard summaries"""

    id = serializers.UUIDField(read_only=True)
    title = serializers.CharField(read_only=True)
    score = serializers.FloatField(read_only=True)
    score_percentage = serializers.FloatField(read_only=True)


class TeacherStudentAnalyticsSerializer(serializers.Serializer):
    """Serializer for a student's comprehensive performance analytics in a course"""

    student_id = serializers.UUIDField(read_only=True)
    student_name = serializers.CharField(read_only=True)
    assignment_submitted = serializers.IntegerField(read_only=True)
    assignment_assigned = serializers.IntegerField(read_only=True)
    average_grade = serializers.FloatField(read_only=True, allow_null=True)
    best_assignment = MiniAssignmentSerializer(read_only=True, allow_null=True)
    worst_assignment = MiniAssignmentSerializer(read_only=True, allow_null=True)
    grade_trend = serializers.CharField(read_only=True)
    assignment_history = StudentAssignmentHistorySerializer(many=True, read_only=True)
    ai_student_summary = serializers.CharField(read_only=True)
    at_risk = serializers.BooleanField(read_only=True)


class SignupTotalsSerializer(serializers.Serializer):
    """Serializer for total signup counts by user type"""

    teachers = serializers.IntegerField(read_only=True)
    students = serializers.IntegerField(read_only=True)
    school_admins = serializers.IntegerField(read_only=True)


class NewSignupStatsSerializer(serializers.Serializer):
    """Serializer for time-based signup aggregates"""

    daily = serializers.IntegerField(read_only=True)
    weekly = serializers.IntegerField(read_only=True)
    monthly = serializers.IntegerField(read_only=True)


class PlatformAdoptionSerializer(serializers.Serializer):
    """Main serializer for platform adoption and adoption metrics"""

    total_signups = SignupTotalsSerializer(read_only=True)
    new_signups = NewSignupStatsSerializer(read_only=True)
    activated_percent = serializers.FloatField(read_only=True)
    active_last_30_days = serializers.IntegerField(read_only=True)
    average_course_per_teacher = serializers.FloatField(read_only=True)
    average_course_size = serializers.FloatField(read_only=True)


class PlatformUsageSerializer(serializers.Serializer):
    """Serializer for platform-wide usage metrics"""

    total_assignments_created = serializers.IntegerField(read_only=True)
    total_assignments_graded = serializers.IntegerField(read_only=True)
    avg_assignments_per_course = serializers.FloatField(read_only=True)
    avg_assignments_per_active_teacher = serializers.FloatField(read_only=True)
    assignment_percent_fully_graded = serializers.FloatField(read_only=True)
    grading_turnaround_time_p50 = serializers.DurationField(
        read_only=True, allow_null=True
    )
    grading_turnaround_time_p95 = serializers.DurationField(
        read_only=True, allow_null=True
    )


class PlatformAIConfidenceSerializer(serializers.Serializer):
    """Serializer for AI confidence and variance metrics"""

    average_extraction = serializers.FloatField(read_only=True)
    average_grading = serializers.FloatField(read_only=True)
    low_confidence_extraction_rate = serializers.FloatField(read_only=True)
    low_confidence_grading_rate = serializers.FloatField(read_only=True)
    confidence_variance_extraction = serializers.FloatField(
        read_only=True, allow_null=True
    )
    confidence_variance_grading = serializers.FloatField(
        read_only=True, allow_null=True
    )


class PlatformAIRiskSerializer(serializers.Serializer):
    """Serializer for AI risk indicators and processing efficiency"""

    manual_override_rate = serializers.FloatField(read_only=True)
    regrade_rate = serializers.FloatField(read_only=True)
    avg_assignment_processing_time = serializers.DurationField(
        read_only=True, allow_null=True
    )
    avg_grading_processing_time = serializers.DurationField(
        read_only=True, allow_null=True
    )


class PlatformAIPerformanceSerializer(serializers.Serializer):
    """Main serializer for platform-wide AI performance analytics"""

    confidence = PlatformAIConfidenceSerializer(read_only=True)
    risk_indicators = PlatformAIRiskSerializer(read_only=True)


class ScalingSignalsSerializer(serializers.Serializer):
    """Serializer for institutional scaling and adoption signals"""

    total_schools = serializers.IntegerField(read_only=True)
    schools_with_multiple_teachers = serializers.IntegerField(read_only=True)
    multi_teacher_adoption_rate = serializers.FloatField(read_only=True)
    avg_teachers_per_school = serializers.FloatField(read_only=True)
    admin_to_teacher_ratio = serializers.FloatField(read_only=True, allow_null=True)
    avg_assignments_per_school = serializers.FloatField(read_only=True)
    avg_grading_confidence_per_school = serializers.FloatField(read_only=True)
    avg_extraction_confidence_per_school = serializers.FloatField(read_only=True)


class SchoolAnalyticsSerializer(serializers.Serializer):
    """Serializer for high-level school performance and engagement metrics"""

    school_id = serializers.UUIDField(read_only=True)
    school_name = serializers.CharField(read_only=True)
    teachers = serializers.IntegerField(read_only=True)
    students = serializers.IntegerField(read_only=True)
    courses = serializers.IntegerField(read_only=True)
    average_performance = serializers.FloatField(read_only=True)


class TeacherPerformanceSerializer(serializers.Serializer):
    """Serializer for high-level teacher performance and engagement metrics"""

    teacher_id = serializers.UUIDField(read_only=True)
    teacher_name = serializers.CharField(read_only=True)
    number_of_courses = serializers.IntegerField(read_only=True)
    number_of_students = serializers.IntegerField(read_only=True)
    average_student_performance = serializers.FloatField(read_only=True)
    assignment_completion_rate = serializers.FloatField(read_only=True)


class SuperAdminStudentPerformanceSerializer(serializers.Serializer):
    """Serializer for global student performance metrics"""

    average_grade = serializers.FloatField(read_only=True)
    global_assignment_completion_rate = serializers.FloatField(read_only=True)
    grade_distribution = GradeDistributionSerializer(read_only=True)
    total_active_enrollments = serializers.IntegerField(read_only=True)


class SchoolAdminSummarySerializer(serializers.Serializer):
    school_name = serializers.CharField(read_only=True)

    teachers = serializers.IntegerField(read_only=True)
    students = serializers.IntegerField(read_only=True)

    assignments_created = serializers.IntegerField(read_only=True)
    assignments_graded = serializers.IntegerField(read_only=True)
    assignments_graded_percentage = serializers.FloatField(read_only=True)

    avg_turnaround_days = serializers.FloatField(
        allow_null=True,
        read_only=True,
    )

    ai_extraction_confidence = serializers.FloatField(read_only=True)
    ai_grading_confidence = serializers.FloatField(read_only=True)

    flagged_for_review_count = serializers.IntegerField(read_only=True)
    flagged_for_review_percentage = serializers.FloatField(read_only=True)

    at_risk_students = serializers.IntegerField(read_only=True)

    avg_courses_per_teacher = serializers.FloatField(read_only=True)
    avg_class_size = serializers.FloatField(read_only=True)
    avg_assignments_per_course = serializers.FloatField(read_only=True)

    student_growth_rate = serializers.FloatField(
        allow_null=True,
        read_only=True,
    )


class AtRiskTrendWeekSerializer(serializers.Serializer):
    """A single weekly data point for the at-risk trend chart."""

    week_start = serializers.DateField(read_only=True)
    week_end = serializers.DateField(read_only=True)
    at_risk_count = serializers.IntegerField(read_only=True)


class SchoolAtRiskTrendSerializer(serializers.Serializer):
    """At-risk student count on a weekly tick over a rolling 2-month window.

    `weeks` only includes weeks that have at least one recorded snapshot -
    weeks before snapshot collection started are omitted rather than
    filled with fabricated 0/null values.
    """

    school_name = serializers.CharField(read_only=True)
    window_start = serializers.DateField(read_only=True)
    window_end = serializers.DateField(read_only=True)
    weeks = AtRiskTrendWeekSerializer(many=True, read_only=True)


class AssignmentActivityOverTimeChartSerializer(serializers.Serializer):
    """
    Serializer for the Assignment Activity Over Time chart.
    """

    labels = serializers.ListField(
        child=serializers.CharField(),
        read_only=True,
        help_text="Month labels (Jan-Dec).",
    )

    created = serializers.ListField(
        child=serializers.IntegerField(),
        read_only=True,
        help_text="Number of assignments created for each month.",
    )

    graded = serializers.ListField(
        child=serializers.IntegerField(),
        read_only=True,
        help_text="Number of assignments that had at least one graded submission in each month.",
    )


class CourseOverviewItemSerializer(serializers.Serializer):
    """
    Represents a single course in the overview chart.
    """

    name = serializers.CharField(read_only=True)
    teachers = serializers.IntegerField(read_only=True)
    avg_grade = serializers.FloatField(
        allow_null=True,
        read_only=True,
    )


class CourseOverviewChartSerializer(serializers.Serializer):
    """
    Response serializer for the Course Overview Chart endpoint.
    """

    courses = CourseOverviewItemSerializer(
        many=True,
        read_only=True,
    )


class SchoolAdminTeacherPerformanceSerializer(serializers.Serializer):
    """Serializer for teacher performance metrics within a school"""

    teacher_id = serializers.UUIDField(read_only=True)
    teacher_name = serializers.CharField(read_only=True)
    number_of_courses = serializers.IntegerField(read_only=True)
    number_of_students = serializers.IntegerField(read_only=True)
    average_student_performance = serializers.FloatField(read_only=True)
    assignment_completion_rate = serializers.FloatField(read_only=True)


class SchoolAdminStudentPerformanceSerializer(serializers.Serializer):
    """Serializer for student performance metrics within a school"""

    school_name = serializers.CharField(read_only=True)
    average_grade = serializers.FloatField(read_only=True)
    assignment_completion_rate = serializers.FloatField(read_only=True)
    grade_distribution = GradeDistributionSerializer(read_only=True)
    total_active_enrollments = serializers.IntegerField(read_only=True)


class RigorBreakdownSerializer(serializers.Serializer):
    """The components behind the blended `rigor` headline.

    Reported separately because the blend alone cannot be acted on: a teacher
    at demand 4.2 / evidence 2.1 (hard questions, everyone scoring highly) and
    one at demand 1.5 / evidence 4.0 (easy questions students still fail) are
    pedagogically opposite situations that collapse to a similar single score.
    """

    score = serializers.FloatField(
        allow_null=True,
        help_text=(
            "Blended 0-5 rigor. Weighted 0.6 demand / 0.25 evidence / 0.15 "
            "standards, renormalized over whichever components are available. "
            "Null when there is no usable Bloom's data."
        ),
    )
    demand = serializers.FloatField(
        allow_null=True,
        help_text=(
            "Mean cognitive demand, 0-5, from per-question Bloom's taxonomy "
            "levels weighted by question points. Remember=0 ... Create=5."
        ),
    )
    evidence = serializers.FloatField(
        allow_null=True,
        help_text=(
            "Difficulty implied by achieved results, 0-5, as "
            "5 * (1 - average score percentage / 100). Null until the teacher "
            "has at least 5 graded submissions."
        ),
    )
    standards = serializers.FloatField(
        allow_null=True,
        help_text=(
            "Share of open-ended questions carrying a rubric of 3+ levels, "
            "scaled 0-5. Null when the teacher sets no open-ended questions."
        ),
    )
    coverage = serializers.FloatField(
        allow_null=True,
        help_text=(
            "Fraction (0.0-1.0) of the teacher's non-draft assignments that "
            "carried usable Bloom's data. Low coverage means the score rests "
            "on a minority of their work."
        ),
    )
    assignments_scored = serializers.IntegerField(
        help_text="Non-draft assignments that contributed to `demand`."
    )
    submissions_scored = serializers.IntegerField(
        help_text="Graded submissions that contributed to `evidence`."
    )
    label = serializers.CharField(
        help_text=(
            "Short plain-language verdict for display, e.g. 'Stretching "
            "students', 'Check the marking', 'Not enough data yet'. Prefer "
            "this over `score` when showing rigor to a human: two scores "
            "that are close can describe opposite situations."
        )
    )
    meaning = serializers.CharField(
        help_text="One-sentence explanation of the verdict, in plain English."
    )
    tone = serializers.ChoiceField(
        choices=["good", "watch", "concern", "neutral", "unknown"],
        help_text="Severity of the verdict, for colour-coding the label.",
    )
    standards_note = serializers.CharField(
        allow_null=True,
        help_text=(
            "Set when most open-ended questions carry no rubric; null " "otherwise."
        ),
    )
    coverage_note = serializers.CharField(
        allow_null=True,
        help_text=(
            "Set when the verdict rests on a minority of the teacher's "
            "assignments; null otherwise."
        ),
    )


class TeacherPerformanceDashboardSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    email = serializers.EmailField()
    courses = serializers.IntegerField()
    students = serializers.IntegerField()
    growth = serializers.FloatField(allow_null=True)
    assignments_per_week = serializers.FloatField(allow_null=True)
    turnaround = serializers.FloatField(allow_null=True)
    ai_confidence = serializers.FloatField(allow_null=True)
    rigor = serializers.FloatField(
        allow_null=True,
        help_text=(
            "Blended academic rigor, 0-5. Same value as "
            "`rigor_breakdown.score`, kept flat for existing consumers."
        ),
    )
    rigor_breakdown = RigorBreakdownSerializer()
    status = serializers.CharField()


class FeatureMixCategorySerializer(serializers.Serializer):
    amount = serializers.IntegerField()
    percent = serializers.FloatField()


class TeacherDailyUsageSerializer(serializers.Serializer):
    date = serializers.DateField()
    credits = serializers.IntegerField()


class TeacherDetailSerializer(TeacherPerformanceDashboardSerializer):
    """
    Everything TeacherPerformanceDashboardSerializer has, plus credit
    usage data for a single teacher's detail view.
    """

    credits_used = serializers.IntegerField(
        help_text="All-time credits consumed by this teacher, net of refunds."
    )
    credits_used_percentage = serializers.FloatField(
        help_text=(
            "credits_used as a percentage of (credits_used + remaining plan "
            "credits). Excludes OVERAGE buckets, which are purchased "
            "reactively and aren't part of the fixed plan allocation."
        )
    )
    days_active = serializers.IntegerField(
        help_text="Distinct calendar days with credit usage in the last 60 days."
    )
    daily_usage = TeacherDailyUsageSerializer(
        many=True, help_text="Daily credit usage for the last 60 days, zero-filled."
    )
    grading = FeatureMixCategorySerializer()
    creation = FeatureMixCategorySerializer()
    feedback = FeatureMixCategorySerializer()
    other = FeatureMixCategorySerializer()


class CourseStudentBreakdownSerializer(serializers.Serializer):
    """Documents the shape of CoursePerformanceDashboardSerializer.students
    (schema-only, not used to actually serialize)."""

    enrolled = serializers.IntegerField()
    completed = serializers.IntegerField()
    pending = serializers.IntegerField()
    total = serializers.IntegerField(
        help_text="enrolled + completed + pending (everyone except withdrawn)."
    )


class CoursePerformanceDashboardSerializer(serializers.ModelSerializer):
    teacher = serializers.CharField(source="teacher.get_full_name")
    students = serializers.SerializerMethodField()
    assignments = serializers.IntegerField(source="assignment_count")
    avg_grade = serializers.FloatField(allow_null=True)
    distribution = serializers.SerializerMethodField()

    class Meta:
        model = Course
        fields = [
            "id",
            "name",
            "teacher",
            "students",
            "assignments",
            "avg_grade",
            "distribution",
        ]

    @extend_schema_field(CourseStudentBreakdownSerializer)
    def get_students(self, obj):
        return {
            "enrolled": obj.enrolled_count,
            "completed": obj.completed_count,
            "pending": obj.pending_count,
            "total": obj.student_count,
        }

    @extend_schema_field(serializers.DictField(child=serializers.IntegerField()))
    def get_distribution(self, obj):
        # Build distribution dict from annotated fields. Only counts
        # enrolled/completed students - see the view for why pending and
        # withdrawn enrollments are excluded.
        grades = {}
        for letter in ["A", "B", "C", "D", "F"]:
            count = getattr(obj, f"grade_{letter}", 0)
            if count:
                grades[letter] = count
        return grades


class CoursePerformanceDashboardPageSerializer(serializers.Serializer):
    """Documents the paginated envelope returned by the course-performance
    dashboard endpoint (schema-only, not used to actually serialize)."""

    count = serializers.IntegerField()
    next = serializers.CharField(allow_null=True)
    previous = serializers.CharField(allow_null=True)
    results = CoursePerformanceDashboardSerializer(many=True)


class UnitPerformanceSerializer(serializers.ModelSerializer):
    course_name = serializers.CharField(source="course.name")
    mastery = serializers.FloatField()
    avg_score = serializers.FloatField(allow_null=True)

    class Meta:
        model = Assignment
        fields = ["id", "title", "course_name", "mastery", "avg_score"]


class CustomAIPrompt(serializers.Serializer):
    """Serializer for custom AI prompts"""

    # 2000 chars is generous for a real question (several paragraphs) while
    # still bounding request size and downstream token/cost exposure - see
    # AIProcessor.custom_ai_prompt, which wraps this text as untrusted data
    # rather than trusting it as instructions.
    prompt = serializers.CharField(required=True, allow_blank=False, max_length=2000)


class CustomAIReply(serializers.Serializer):
    """Serializer for custom AI templates"""

    response = serializers.CharField(required=True)


class DashboardChatMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChatMessage
        fields = ["id", "role", "content", "timestamp"]
        read_only_fields = fields


class DashboardChatSessionSerializer(serializers.ModelSerializer):
    messages = DashboardChatMessageSerializer(
        many=True, read_only=True, source="chatmessage_set"
    )

    class Meta:
        model = ChatSession
        fields = [
            "id",
            "assistant_type",
            "created_at",
            "updated_at",
            "messages",
        ]
        read_only_fields = fields
