from rest_framework import serializers

from .models import Assignment, Rubric


class RubricSerializer(serializers.ModelSerializer):
    class Meta:
        model = Rubric
        fields = ["id", "criteria", "created_at", "updated_at"]
        read_only_fields = ["created_at", "updated_at"]


class AssignmentSerializer(serializers.ModelSerializer):
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = Assignment
        fields = [
            "id",
            "title",
            "subject_name",
            "instructions",
            "total_points",
            "question_count",
            "assignment_type",
            "created_at",
            "created_by",
            "questions",
        ]
        read_only_fields = [
            "created_at",
        ]

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
            return serializers.ValidationError(
                "Question cannot be empty"
            )  # or raise an error depending on your use case

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
            # validated_data['created_by'] = request.user
        return super().create(validated_data)
