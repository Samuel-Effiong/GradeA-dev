from rest_framework import serializers

from assignments.models import Assignment


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


class StudentAssignmentListSerializer(serializers.ModelSerializer):
    assignment = serializers.IntegerField(source="id", read_only=True)
    score = serializers.CharField()
    submission_date = serializers.DateTimeField()
    feedback = serializers.CharField()
    submission_status = serializers.CharField(max_length=30)

    class Meta:
        model = Assignment
        fields = [
            "assignment",
            "title",
            "due_date",
            "submission_date",
            "score",
            "submission_status",
            "feedback",
        ]


class AssignmentPerformanceSerializer(serializers.Serializer):
    """Serializer for best/worst assignment entries in the student dashboard"""

    assignment_id = serializers.UUIDField(source="assignment.id", read_only=True)
    assignment_name = serializers.CharField(source="assignment.title", read_only=True)
    score = serializers.FloatField(read_only=True)


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


class TeacherDashboardOverviewSerializer(serializers.Serializer):
    """Serializer for the teacher dashboard overview metrics"""

    total_assignments_assigned = serializers.IntegerField(read_only=True)
    total_assignments_graded = serializers.IntegerField(read_only=True)
    percentage_graded = serializers.FloatField(read_only=True)
    average_grading_turnaround = serializers.DurationField(read_only=True)


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


class AITrustStatsSerializer(serializers.Serializer):
    """Serializer for AI trust and confidence metrics"""

    average_ai_extraction_confidence = serializers.FloatField(read_only=True)
    average_ai_grading_confidence = serializers.FloatField(read_only=True)
    low_confidence_rate = serializers.FloatField(read_only=True)


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
    graded_at = serializers.DateTimeField(read_only=True, allow_null=True)


class MiniAssignmentSerializer(serializers.Serializer):
    """Minimal assignment serializer for dashboard summaries"""

    id = serializers.UUIDField(read_only=True)
    title = serializers.CharField(read_only=True)
    score = serializers.FloatField(read_only=True)


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


class GradeDistributionSerializer(serializers.Serializer):
    """Serializer for A-F grade distribution"""

    A = serializers.IntegerField(read_only=True)
    B = serializers.IntegerField(read_only=True)
    C = serializers.IntegerField(read_only=True)
    D = serializers.IntegerField(read_only=True)
    F = serializers.IntegerField(read_only=True)


class SuperAdminStudentPerformanceSerializer(serializers.Serializer):
    """Serializer for global student performance metrics"""

    average_grade = serializers.FloatField(read_only=True)
    global_assignment_completion_rate = serializers.FloatField(read_only=True)
    grade_distribution = GradeDistributionSerializer(read_only=True)
    total_active_enrollments = serializers.IntegerField(read_only=True)


class SchoolAdminSummarySerializer(serializers.Serializer):
    """Serializer for school admin dashboard overview metrics"""

    school_name = serializers.CharField(read_only=True)
    active_teachers = serializers.IntegerField(read_only=True)
    active_students = serializers.IntegerField(read_only=True)
    active_courses = serializers.IntegerField(read_only=True)
    total_assignments = serializers.IntegerField(read_only=True)
    total_submissions = serializers.IntegerField(read_only=True)


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
