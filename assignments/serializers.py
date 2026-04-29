import json

from django.db import transaction
from django.utils import timezone
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework.exceptions import ParseError

from classrooms.models import Course, StudentCourse, Topic
from students.models import StudentSubmission
from users.models import UserTypes

from .models import (  # Rubric
    Assignment,
    AssignmentGenerationHistory,
    AssignmentGenerationMessage,
    AssignmentGenerationRole,
    AssignmentGenerationSession,
    AssignmentStatus,
)


class AssignmentRubricSerializer(serializers.Serializer):
    level = serializers.CharField()
    points = serializers.FloatField()
    description = serializers.CharField()


class StudentSubmissionStatusSerializer(serializers.Serializer):
    """Serializer for the student submission items in the assignment details"""

    submission_id = serializers.UUIDField(read_only=True, allow_null=True)
    name = serializers.CharField(read_only=True)
    email = serializers.EmailField(read_only=True)
    submission_status = serializers.CharField(read_only=True)
    grade = serializers.FloatField(read_only=True, allow_null=True)
    grade_percentage = serializers.FloatField(read_only=True, allow_null=True)
    max_points = serializers.IntegerField(read_only=True, allow_null=True)
    grade_status = serializers.CharField(read_only=True)
    is_published = serializers.BooleanField(read_only=True)
    # teacher_feedback = serializers.CharField(read_only=True, allow_null=True)


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
    submission_count = serializers.SerializerMethodField()

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
            "auto_grade_on_due_date",
            "teacher",
            "submission_count",
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
        read_only_fields = ["created_at", "id", "submission_count"]

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

        # if questions and "question_count" in data:
        #     if len(questions) != data["question_count"]:
        #         raise serializers.ValidationError(
        #             "Question count does not match the number of questions provided."
        #         )

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

    def get_submission_count(self, obj):
        if obj.status == AssignmentStatus.PUBLISHED or obj.submissions:
            return obj.submissions.count()
        return 0


class AssignmentListSerializer(serializers.ModelSerializer):
    submission_count = serializers.SerializerMethodField()
    is_grading_scheduled = serializers.SerializerMethodField()

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
            "auto_grade_on_due_date",
            "extraction_confidence",
            "submission_count",
            "scheduled_grading_at",
            "grading_task_name",
            "is_grading_scheduled",
        ]
        read_only_fields = [
            "created_at",
            "id",
            "submission_count",
            "scheduled_grading_at",
            "grading_task_name",
            "is_grading_scheduled",
        ]

    def get_submission_count(self, obj):
        if obj.status == AssignmentStatus.PUBLISHED or obj.submissions:
            return obj.submissions.count()
        return 0

    def get_is_grading_scheduled(self, obj) -> bool:
        return bool(
            obj.scheduled_grading_at and obj.scheduled_grading_at > timezone.now()
        )


class AssignmentDetailSerializer(serializers.ModelSerializer):
    student_submissions = serializers.SerializerMethodField()
    raw_input = serializers.SerializerMethodField()
    is_grading_scheduled = serializers.SerializerMethodField()

    class Meta:
        model = Assignment
        fields = [
            "id",
            "title",
            "course",
            "topic",
            "status",
            "raw_input",
            "created_at",
            "due_date",
            "auto_grade_on_due_date",
            "extraction_confidence",
            "assignment_type",
            "total_points",
            "question_count",
            "student_submissions",
            "scheduled_grading_at",
            "grading_task_name",
            "is_grading_scheduled",
        ]
        read_only_fields = [
            "created_at",
            "due_date",
            "auto_grade_on_due_date",
            "extraction_confidence",
            "assignment_type",
            "status",
            "total_points",
            "question_count",
            "student_submissions",
            "scheduled_grading_at",
            "grading_task_name",
            "is_grading_scheduled",
        ]

    def get_raw_input(self, obj):
        """
        Return the full raw_input for teachers.
        For students, regenerate the ProseMirror JSON from the structured
        questions data with rubric and model answer excluded — so the hidden
        content is determined at generation time rather than by fragile
        post-processing of the stored JSON.
        """
        request = self.context.get("request")
        if request and getattr(request.user, "user_type", None) == UserTypes.STUDENT:
            # Import here to avoid circular imports at module level
            from .services import AssignmentProcessingService

            if not obj.questions:
                return obj.raw_input

            data = {
                "title": obj.title,
                "instructions": obj.instructions,
                "total_points": obj.total_points,
                "due_date": obj.due_date.isoformat() if obj.due_date else None,
                "questions": obj.questions,
            }
            student_html = AssignmentProcessingService.format_assignment_standard_html(
                data, include_rubric=False
            )
            pm_json = AssignmentProcessingService.html_to_prosemirror_json(student_html)
            return json.dumps(pm_json)

        return obj.raw_input

    @extend_schema_field(StudentSubmissionStatusSerializer(many=True))
    def get_student_submissions(self, obj):

        # Fetch all enrolled students for this course
        enrollments = StudentCourse.objects.filter(course=obj.course).select_related(
            "student"
        )

        # Build a lookup map: student_id → submission
        submission_map = {
            sub.student_id: sub
            for sub in StudentSubmission.objects.filter(assignment=obj).select_related(
                "student"
            )
        }

        result = []
        for enrollment in enrollments:
            student = enrollment.student
            submission = submission_map.get(student.id)

            # Submission status
            submission_status = "SUBMITTED" if submission else "NOT SUBMITTED"
            submission_id = submission.id if submission else None

            # Grade — prefer human score, fall back to AI score
            grade = None
            grade_percentage = None
            if submission:
                if submission.score_percentage is not None:
                    grade_percentage = float(submission.score_percentage)

                if submission.score is not None:
                    grade = float(submission.score)
                elif submission.ai_score is not None:
                    grade = float(submission.ai_score)

            # Grade status
            grade_status = "N/A"
            if submission:
                if submission.graded_at is None:
                    grade_status = "NOT GRADED"
                else:
                    grade_status = "GRADED"

            result.append(
                {
                    "submission_id": submission_id,
                    "name": student.get_full_name(),
                    "email": student.email,
                    "submission_status": submission_status,
                    "grade": grade,
                    "grade_percentage": grade_percentage,
                    "max_points": submission.max_points if submission else None,
                    "grade_status": grade_status,
                    "wasw_regraded": submission.was_regraded if submission else None,
                    "is_published": submission.is_published if submission else False,
                    # "teacher_feedback": (
                    #     submission.formatted_grade if submission else None
                    # ),
                }
            )

        return result

    def get_is_grading_scheduled(self, obj) -> bool:
        return bool(
            obj.scheduled_grading_at and obj.scheduled_grading_at > timezone.now()
        )


class GeneratedAssignmentSerializer(serializers.Serializer):
    content = serializers.CharField()
    assignment_id = serializers.UUIDField(required=False, allow_null=True)
    session_id = serializers.UUIDField(required=False, allow_null=True)
    message_id = serializers.UUIDField(required=False, allow_null=True)


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
    due_date = serializers.DateTimeField(required=False, allow_null=True)
    auto_grade_on_due_date = serializers.BooleanField(required=False, default=False)

    def validate_due_date(self, value):
        if value and value < timezone.now():
            raise serializers.ValidationError("Due date cannot be in the past.")
        return value

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

    def update(self, instance, validated_data):
        instance.title = validated_data.get("title", instance.title)
        instance.course = validated_data.get("course", instance.course)
        instance.topic = validated_data.get("topic", instance.topic)
        instance.status = validated_data.get("status", instance.status)
        instance.due_date = validated_data.get("due_date", instance.due_date)
        instance.auto_grade_on_due_date = validated_data.get(
            "auto_grade_on_due_date", instance.auto_grade_on_due_date
        )
        instance.save()
        return instance


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
    task_id = serializers.UUIDField()
    message = serializers.CharField(max_length=255)


class AssignmentGradeAllSubmissionsSerializer(serializers.Serializer):
    """
    Serializer for the response return after starting the grading of
    all submissions for an assignment
    """

    assignment_id = serializers.UUIDField()
    task_id = serializers.UUIDField()
    message = serializers.CharField(max_length=255)
    submission_count = serializers.IntegerField()
    status = serializers.CharField(max_length=255)


class ScheduleGradingSerializer(serializers.Serializer):
    schedule_time = serializers.DateTimeField()


class ScheduledGradingResponseSerializer(serializers.Serializer):
    session_id = serializers.UUIDField(required=False)
    period_task_id = serializers.UUIDField()
    task_name = serializers.CharField()
    scheduled_time = serializers.DateTimeField()
    message = serializers.CharField()


class PublishAllGradesResponseSerializer(serializers.Serializer):
    message = serializers.CharField(help_text="Success message detailing the action")
    total_graded = serializers.IntegerField(
        min_value=0, help_text="Total number of submissions that were graded"
    )
    ungraded_count = serializers.IntegerField(
        min_value=0, help_text="Total number of submissions that were not graded"
    )


class TaskInfoSerializer(serializers.Serializer):
    """
    Serializer for individual task information within a batch.
    """

    file_name = serializers.CharField()
    task_id = serializers.UUIDField()


class BatchUploadResponseSerializer(serializers.Serializer):
    """
    Serializer for the response returned after starting a batch upload.
    Includes a mapping of filenames to their respective task IDs.
    """

    session_id = serializers.UUIDField()
    message = serializers.CharField()
    tasks = TaskInfoSerializer(many=True)


class AssignmentGenerationMessageSerializer(serializers.ModelSerializer):
    assignment_title = serializers.CharField(
        source="assignment.title",
        read_only=True,
    )

    class Meta:
        model = AssignmentGenerationMessage
        fields = [
            "id",
            "session",
            "role",
            "content",
            "assignment",
            "assignment_title",
            "assignment_snapshot",
            "metadata",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "created_at",
            "assignment_title",
        ]


class AssignmentGenerationMessageCreateSerializer(serializers.Serializer):
    content = serializers.CharField(allow_blank=False, trim_whitespace=True)
    role = serializers.ChoiceField(
        choices=AssignmentGenerationRole.choices,
        default=AssignmentGenerationRole.USER,
    )


class AssignmentGenerationSessionSerializer(serializers.ModelSerializer):
    course_name = serializers.CharField(source="course.name", read_only=True)
    user_name = serializers.CharField(source="user.get_full_name", read_only=True)
    message_count = serializers.SerializerMethodField()
    latest_message_preview = serializers.SerializerMethodField()

    class Meta:
        model = AssignmentGenerationSession
        fields = [
            "id",
            "user",
            "user_name",
            "course",
            "course_name",
            "title",
            "message_count",
            "latest_message_preview",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "user_name",
            "course_name",
            "message_count",
            "latest_message_preview",
            "created_at",
            "updated_at",
        ]

    def get_message_count(self, obj):
        return getattr(obj, "message_count", None) or obj.messages.count()

    def get_latest_message_preview(self, obj):
        latest_message = getattr(obj, "latest_message", None)

        if latest_message is None:
            latest_message = obj.messages.order_by("-created_at").first()

        if not latest_message:
            return None

        content = latest_message.content or ""
        return content[:140]


class AssignmentGenerationSessionCreateSerializer(serializers.Serializer):
    title = serializers.CharField(required=False, allow_blank=True, max_length=255)


class AssignmentGenerationSessionDetailSerializer(serializers.ModelSerializer):
    course_name = serializers.CharField(source="course.name", read_only=True)
    user_name = serializers.CharField(source="user.get_full_name", read_only=True)
    messages = AssignmentGenerationMessageSerializer(many=True, read_only=True)

    class Meta:
        model = AssignmentGenerationSession
        fields = [
            "id",
            "user",
            "user_name",
            "course",
            "course_name",
            "title",
            "messages",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "user_name",
            "course_name",
            "messages",
            "created_at",
            "updated_at",
        ]


class AssignmentGenerationHistorySerializer(serializers.ModelSerializer):
    """
    Serializer for the AssignmentGenerationHistory model.

    This serializer provides a simplified view of the history entry,
    including the prompt and a summary of the generated assignment.
    """

    assignment_title = serializers.CharField(
        source="assignment.title",
        read_only=True,
        help_text="Title of the generated assignment",
    )
    assignment_questions_count = serializers.IntegerField(
        source="assignment.question_count",
        read_only=True,
        help_text="Number of questions in the generated assignment",
    )
    assignment_type = serializers.CharField(
        source="assignment.assignment_type",
        read_only=True,
        help_text="Type of the generated assignment",
    )
    course_name = serializers.CharField(
        source="course.name", read_only=True, help_text="Name of the course"
    )
    topic_name = serializers.CharField(
        source="topic.name",
        read_only=True,
        allow_null=True,
        help_text="Name of the topic (if any)",
    )

    class Meta:
        model = AssignmentGenerationHistory
        fields = [
            "id",
            "prompt",
            "assignment",
            "assignment_title",
            "assignment_questions_count",
            "assignment_type",
            "course",
            "course_name",
            "topic",
            "topic_name",
            "generation_mode",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "assignment_title",
            "assignment_questions_count",
            "assignment_type",
            "course_name",
            "topic_name",
            "created_at",
        ]


class AssignmentGenerationHistoryDetailSerializer(serializers.ModelSerializer):
    """
    Detailed serializer for AssignmentGenerationHistory.

    Includes the full assignment object alongside the prompt and metadata.
    """

    assignment = AssignmentListSerializer(read_only=True)
    course_name = serializers.CharField(
        source="course.name", read_only=True, help_text="Name of the course"
    )
    topic_name = serializers.CharField(
        source="topic.name",
        read_only=True,
        allow_null=True,
        help_text="Name of the topic (if any)",
    )
    user_name = serializers.CharField(
        source="user.get_full_name",
        read_only=True,
        help_text="Name of the user who generated the assignment",
    )

    class Meta:
        model = AssignmentGenerationHistory
        fields = [
            "id",
            "prompt",
            "assignment",
            "course",
            "course_name",
            "topic",
            "topic_name",
            "user_name",
            "generation_mode",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "assignment",
            "course_name",
            "topic_name",
            "user_name",
            "created_at",
        ]
