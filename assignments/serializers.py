from django.db import transaction
from rest_framework import serializers

from .models import Assignment, Rubric


class AssignmentRubricSerializer(serializers.Serializer):
    level = serializers.CharField()
    points = serializers.FloatField()
    description = serializers.CharField()


class QuestionSerializer(serializers.Serializer):
    ALLOWED_QUESTION_TYPES = {"OBJECTIVE", "ESSAY", "SHORT-ANSWER"}
    BLOOMS_LEVEL = {"Remember", "Understand", "Apply", "Analyze", "Evaluate", "Create"}

    question_number = serializers.IntegerField(required=True)
    question_text = serializers.CharField(required=True)
    question_type = serializers.CharField(required=True)
    question_image = serializers.CharField(required=True, allow_blank=True)
    points = serializers.FloatField(required=True)
    blooms_level = serializers.CharField(required=False, allow_blank=True)
    options = serializers.ListField(
        child=serializers.CharField(),
        required=True,
        allow_empty=True,
    )
    rubric = AssignmentRubricSerializer(many=True, required=True)
    model_answer = serializers.CharField(required=False, allow_blank=True)

    def validate_question_type(self, value):
        if value not in self.ALLOWED_QUESTION_TYPES:
            raise serializers.ValidationError(
                f"Invalid question_type `{value}`. Allowed types: {', '.join(self.ALLOWED_QUESTION_TYPES)}"
            )
        return value

    def validate_blooms_level(self, value):
        if value not in self.BLOOMS_LEVEL:
            raise serializers.ValidationError(
                f"Invalid blooms_level `{value}`. Allow types: {', '.join(self.BLOOMS_LEVEL)}"
            )


class AssignmentSerializer(serializers.ModelSerializer):
    questions = serializers.ListField(
        child=QuestionSerializer(),
        min_length=1,
        required=True,
    )

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
            "due_date",
            "teacher",
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

    def validate(self, data):
        questions = data.get("questions", [])

        if questions and "question_count" in data:
            if len(questions) != data["question_count"]:
                raise serializers.ValidationError(
                    "Question count does not match the number of questions provided."
                )

        assignment_type = data.get("assignment_type")
        if assignment_type and assignment_type != "HYBRID":
            # Only OBJECTIVE, ESSAY, SHORT-ANSWER require uniform question types
            for i, question in enumerate(questions):
                q_type = question.get("question_type")
                if q_type != assignment_type:
                    raise serializers.ValidationError(
                        f"assignment_type `{assignment_type}` requires all questions to have question_type "
                        f"`{assignment_type}`. Question {i} has question_type `{q_type}`."
                    )
        return data

    def create(self, validated_data):
        request = self.context.get("request")
        if request and hasattr(request, "user"):
            pass
            # TODO: validated_data['created_by'] = request.user

        try:
            with transaction.atomic():
                # Create the assignment instance with remaining validated data
                # assignment = Assignment.objects.create(**validated_data)
                assignment = super().create(validated_data)

                questions = validated_data.get("questions", [])

                criteria = []
                for question in questions:
                    criterion = {
                        "question_number": question.get("question_number"),
                        "question_text": question.get("question_text"),
                        "points": question.get("points"),
                        "model_answer": question.get("model_answer", ""),
                        "rubric": question.get("rubric", []),
                    }
                    criteria.append(criterion)

                Rubric.objects.create(
                    assignment=assignment,
                    criteria=criteria,
                )

                return assignment
        except Exception as e:
            raise serializers.ValidationError(
                f"Failed to create assignment and rubric: {e}"
            ) from Exception


class ScoringLevelSerializer(serializers.Serializer):
    level = serializers.CharField()
    points = serializers.FloatField()
    description = serializers.CharField()


class CriterionSerializer(serializers.Serializer):
    question_number = serializers.IntegerField()
    question_text = serializers.CharField()
    points = serializers.FloatField()
    model_answer = serializers.CharField(allow_blank=True)
    rubric = serializers.ListField(
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
        if "criteria" in data and "points" in data:
            total_max_points = sum(
                criteria.get("points", 0) for criteria in data["criteria"]
            )
            if abs(total_max_points - data["points"]) > 0.01:
                raise serializers.ValidationError(
                    f"Sum of points in criteria ({total_max_points}) "
                    f"doesn't match points ({data['points']})"
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
