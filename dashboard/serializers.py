from rest_framework import serializers

from assignments.models import Assignment


class PeakTimeSerializer(serializers.Serializer):
    hour = serializers.IntegerField()
    label = serializers.CharField()
    average_users = serializers.FloatField()


class ConcurrencySerializer(serializers.Serializer):
    range = serializers.CharField()
    start = serializers.DateTimeField()
    end = serializers.DateTimeField()
    peak_concurrent_users = serializers.IntegerField()
    peak_time_of_day = PeakTimeSerializer(allow_null=True, required=False)


class AssignmentPerformanceSerializer(serializers.Serializer):
    """Child serializer for the best/worst assignment entries"""

    assignment_id = serializers.UUIDField()
    assignment_name = serializers.CharField(source="assignment.title")
    score = serializers.FloatField()


class StudentAssignmentListSerializer(serializers.ModelSerializer):
    assignment_id = serializers.PrimaryKeyRelatedField(read_only=True)
    score = serializers.CharField()
    submission_date = serializers.DateTimeField()
    feedback = serializers.CharField()
    submission_status = serializers.CharField(max_length=30)

    class Meta:
        model = Assignment
        fields = [
            "assignment_id",
            "title",
            "due_date",
            "submission_date",
            "score",
            "submission_status",
            "feedback",
        ]


class CourseAnalyticsSerializer(serializers.Serializer):
    """Parent serializer for the main dashboard response"""

    course = serializers.UUIDField()
    assignment_submitted = serializers.IntegerField()
    assignment_assigned = serializers.IntegerField()
    completion_rate = serializers.FloatField()
    missing_or_overdue = serializers.IntegerField()
    average_grade = serializers.FloatField()
    grade_trend = serializers.CharField()

    best_assignments = AssignmentPerformanceSerializer(many=True)
    worst_assignments = AssignmentPerformanceSerializer(many=True)
