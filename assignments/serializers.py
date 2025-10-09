from django.db import transaction
from rest_framework import serializers

from .models import Assignment, Rubric


class AssignmentSerializer(serializers.ModelSerializer):
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = Assignment
        fields = [
            "id",
            "course",
            "title",
            "instructions",
            "total_points",
            "question_count",
            "assignment_type",
            "created_at",
            "created_by",
            "questions",
        ]
        read_only_fields = ["created_at", "id"]

    def validate_total_points(self, value: int | float) -> int | float:
        if value <= 0:
            raise serializers.ValidationError("Total points must be greater than 0")
        return value

    def validate_question_count(self, value: int) -> int:
        if value <= 0:
            raise serializers.ValidationError("Question count must be greater than 0")
        return value

    def validate_questions(self, value):
        if value is not None and not isinstance(value, list):
            raise serializers.ValidationError("Questions must be a list")

        if value is None:
            raise serializers.ValidationError("Question cannot be empty")

        for i, question in enumerate(value, 1):
            if not isinstance(question, dict):
                raise serializers.ValidationError(f"Question {i} must be a dictionary")

            required_fields = ["question_text", "question_type", "points", "options"]
            missing_fields = [
                field for field in required_fields if field not in question
            ]

            if missing_fields:
                raise serializers.ValidationError(
                    f"Question {i} is missing fields: {', '.join(missing_fields)}"
                )

            if not isinstance(question["options"], list):
                raise serializers.ValidationError(
                    f"Question {i} options must be a list"
                )

            if not isinstance(question["points"], (int, float)):
                raise serializers.ValidationError(
                    f"Question {i} point must be a number"
                )

        return value

    def create(self, validated_data):
        request = self.context.get("request")
        if request and hasattr(request, "user"):
            pass
            # TODO: validated_data['created_by'] = request.user
        return super().create(validated_data)


class ScoringLevelSerializer(serializers.Serializer):
    level = serializers.CharField()
    points = serializers.FloatField()
    description = serializers.CharField()


class CriterionSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    question = serializers.CharField()
    max_points = serializers.FloatField()
    model_answer = serializers.CharField(allow_blank=True)
    scoring_levels = serializers.ListField(
        child=ScoringLevelSerializer(),
        min_length=1,
        required=True,
    )


class RubricSerializer(serializers.ModelSerializer):
    assignment = serializers.PrimaryKeyRelatedField(queryset=Assignment.objects.all())
    criteria = serializers.ListField(
        child=CriterionSerializer(),
        min_length=1,
        required=True,
    )

    class Meta:
        model = Rubric
        fields = ["id", "assignment", "criteria", "created_at", "updated_at"]
        read_only_fields = ["created_at", "updated_at", "id"]

    def validate(self, data):
        if "criteria" in data and "max_points" in data:
            total_max_points = sum(
                criteria.get("max_points", 0) for criteria in data["criteria"]
            )
            if abs(total_max_points - data["max_points"]) > 0.01:
                raise serializers.ValidationError(
                    f"Sum of max_points in criteria ({total_max_points}) "
                    f"doesn't match max_points ({data['max_points']})"
                )
        return data

    def create(self, validated_data):
        # Extract nested data

        try:
            with transaction.atomic():
                criteria = validated_data.pop("criteria")

                rubric = Rubric.objects.create(**validated_data, criteria=criteria)

                return rubric
        except Exception as e:
            raise serializers.ValidationError(f"An error occurred: {e}") from Exception
