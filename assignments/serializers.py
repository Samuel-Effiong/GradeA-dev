import json

from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import ParseError

from classrooms.models import Course, Topic

from .models import Assignment, AssignmentStatus  # Rubric


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
            "topic",
            "title",
            "instructions",
            "total_points",
            "question_count",
            "assignment_type",
            "status",
            "created_at",
            "due_date",
            "teacher",
            "questions",
            "raw_input",
            "potential_issues",
            "self_assessment",
            "extraction_confidence",
            "ai_generated",
            "ai_raw_payload",
            "ai_generated_at",
            "extraction_started_at",
            "extraction_completed_at",
        ]
        read_only_fields = ["created_at", "id"]

        extra_kwargs = {
            "title": {"required": False},
            "ai_generated": {"write_only": True},
            "ai_raw_payload": {"write_only": True},
            "ai_generated_at": {"write_only": True},
            "extraction_started_at": {"write_only": True},
            "extraction_completed_at": {"write_only": True},
            "raw_input": {"required": False},
        }

    def validate_total_points(self, value: int | float) -> int | float:
        if value <= 0:
            raise serializers.ValidationError("Total points must be greater than 0")
        return value

    def validate_question_count(self, value: int) -> int:
        if value <= 0:
            raise serializers.ValidationError("Question count must be greater than 0")
        return value

    def validate(self, data):

        topic = data.get("topic")
        course = data.get("course")

        if topic and topic.course != course:
            raise serializers.ValidationError(
                "Topic must belong to the same course as the assignment"
            )

        # del data['course']

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

                # Rubric.objects.create(
                #     assignment=assignment,
                #     criteria=criteria,
                # )

                return assignment
        except Exception as e:
            raise serializers.ValidationError(
                f"Failed to create assignment and rubric: {e}"
            ) from Exception

    def normalize(self, obj):
        return json.loads(json.dumps(obj, sort_keys=True))

    def update(self, instance, validated_data):
        if instance.ai_generated and instance.ai_raw_payload:
            ai_snapshot = self.normalize(
                {
                    "title": instance.ai_raw_payload.get("title"),
                    "instructions": instance.ai_raw_payload.get("instructions"),
                    "questions": instance.ai_raw_payload.get("questions"),
                }
            )

            teacher_version = self.normalize(
                {
                    "title": validated_data.get("title", instance.title),
                    "instructions": validated_data.get(
                        "instructions", instance.instructions
                    ),
                    "questions": validated_data.get("questions", instance.questions),
                }
            )

            if ai_snapshot != teacher_version:
                instance.was_overridden = True
                instance.overridden_at = timezone.now()
        return super().update(instance, validated_data)


class AssignmentListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Assignment
        fields = [
            "id",
            "course",
            "topic",
            "title",
            "instructions",
            "total_points",
            "question_count",
            "assignment_type",
            "status",
            "created_at",
            "due_date",
            "extraction_confidence",
        ]


class AssignmentDetailSerializer(serializers.ModelSerializer):
    class Meta:
        model = Assignment
        fields = ["id", "title", "course", "topic", "status", "raw_input"]


class GeneratedAssignmentSerializer(serializers.Serializer):
    content = serializers.CharField()


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


class AssignmentTextSerializer(serializers.Serializer):
    title = serializers.CharField(required=False, allow_null=True)
    course = serializers.PrimaryKeyRelatedField(
        queryset=Course.objects.all(), required=True
    )
    topic = serializers.PrimaryKeyRelatedField(
        queryset=Topic.objects.all(), required=False, allow_null=True
    )
    status = serializers.CharField(default="DRAFT", required=False)
    raw_input = serializers.CharField(
        required=True, allow_blank=False, allow_null=False
    )

    def validate_raw_input(self, value):
        if not value.strip():
            raise ParseError("Assignment content cannot be empty")
        return value.strip()

    def validate_status(self, value):
        valid_statuses = [choice[0] for choice in AssignmentStatus.choices]
        if value not in valid_statuses:
            raise serializers.ValidationError(
                f"Invalid status `{value}`. Allowed statuses: {', '.join(valid_statuses)}"
            )
        return value

    def validate(self, data):
        if self.partial:
            topic = data.get("topic", self.instance.topic)
            course = data.get("course", self.instance.course)
        else:
            topic = data.get("topic")
            course = data.get("course")

        if topic and topic.course != course:
            raise serializers.ValidationError(
                "Topic must belong to the selected course."
            )
        return data


class StatusMessageSerializer(serializers.Serializer):
    """
    A generic serializer for simple API feedback.
    Example: {'status': 'success', 'message': 'Operation completed'}
    Example: {'status': 'error', 'message': 'Invalid credentials'}
    """

    status = serializers.BooleanField(default=True)
    message = serializers.CharField(max_length=255)


class AssignmentCreateResponseSerializer(serializers.Serializer):
    """
    Serializer for the response returned after starting assignment extraction.
    """

    assignment_id = serializers.UUIDField()
    task_id = serializers.CharField(max_length=255)
    message = serializers.CharField(max_length=255)


class AssignmentGradeAllSubmissions(serializers.Serializer):
    """
    Serializer for the response return after starting the grading of
    all submissions for an assignment
    """

    assignment_id = serializers.UUIDField()
    task_id = serializers.CharField(max_length=255)
    message = serializers.CharField(max_length=255)
    submission_count = serializers.IntegerField()
    status = serializers.CharField(max_length=255)
