import hashlib
import json
import logging
import re
import uuid
from io import BytesIO

from django.core.files.uploadedfile import UploadedFile
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.html import escape as escape_html
from django_celery_beat.models import ClockedSchedule, PeriodicTask

# from django.utils.decorators import method_decorator
# from django.views.decorators.cache import cache_page
# from django.views.decorators.vary import vary_on_headers
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
)
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import (
    NotFound,
    ParseError,
    PermissionDenied,
    ValidationError,
)
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from ai_processor.serializers import AssignmentGeneratorSerializer
from ai_processor.services import ai_processor  # pdf_service
from AutoGrader.error_messages import describe_user_error
from AutoGrader.pagination import StandardPageNumberPagination
from AutoGrader.uploads import PayloadTooLarge, validate_upload_size
from billing.access_control import AIFeatureNotAvailableError
from billing.errors import InsufficientCreditsError

# from ai_processor.tools import encode_image
from classrooms.models import Course, Topic
from classrooms.permissions import IsTeacher, IsTeacherOrReadOnly
from classrooms.serializers import TopicSerializer
from students.models import BackgroundTaskType, BatchUploadSession, BatchUploadType
from students.task_tracking import create_processing_task, launch_processing_task
from users.mixins import UserCacheMixin

# from students.serializers import StudentSubmissionSerializer
from users.models import UserTypes
from users.permissions import HasCreditBalance

from .models import (  # Rubric
    Assignment,
    AssignmentGenerationMessage,
    AssignmentGenerationRole,
    AssignmentGenerationSession,
    AssignmentStatus,
)
from .pdf_cache import get_cached_pdf, store_pdf
from .pdf_renderer import render_html_to_pdf
from .serializers import (  # RubricSerializer,; AssignmentGradeAllSubmissionsSerializer,
    AssignmentCreateResponseSerializer,
    AssignmentDetailSerializer,
    AssignmentDetailStudentSerializer,
    AssignmentGenerationSessionDetailSerializer,
    AssignmentGenerationSessionSerializer,
    AssignmentListSerializer,
    AssignmentListStudentSerializer,
    AssignmentSerializer,
    AssignmentTextSerializer,
    BatchUploadResponseSerializer,
    GeneratedAssignmentSerializer,
    PublishAllGradesResponseSerializer,
    SaveGeneratedAssignmentDraftSerializer,
    ScheduledGradingResponseSerializer,
    ScheduleGradingSerializer,
)
from .services import AssignmentProcessingService, _strip_html_from_title
from .tasks import (  # grade_all_submissions,
    extract_assignment_background_task,
    grade_engine_async,
    update_assignment_background_task,
    upload_assignment_async,
)

logger = logging.getLogger(__name__)

# from billing.access_control import require_ai_access


# from students.models import StudentSubmission


# from ai_processor.validators import AssignmentStructure

# from assignments.services import PDFService


@extend_schema_view(
    list=extend_schema(
        tags=["Assignments"],
        summary="List all assignments",
        parameters=[
            OpenApiParameter(
                name="page",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Page number for pagination",
            ),
            OpenApiParameter(
                name="page_size",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Number of results per page (max 100)",
            ),
        ],
        responses={
            200: AssignmentListSerializer(many=True),  # Use the standard serializer
        },
        examples=[
            OpenApiExample(
                "Teacher List View",
                value=[
                    {
                        "id": f"{uuid.uuid4()}",
                        "course": "Dummy Course",
                        "topic": "Dummy Topic",
                        "title": "Dummy Assignment",
                        "instructions": "Dummy Instructions",
                        "total_points": 100,
                        "question_count": 1,
                        "assignment_type": "ESSAY",
                        "status": "PENDING",
                        "created_at": timezone.now(),
                        "due_date": timezone.now(),
                        "auto_grade_on_due_date": False,
                        "extraction_confidence": 100,
                        "submission_count": 4,
                        "scheduled_grading_at": None,
                        "grading_task_name": None,
                        "is_grading_scheduled": False,
                    }
                ],
                response_only=True,
                status_codes=[200],
            ),
            OpenApiExample(
                "Student List View",
                value=[
                    {
                        "id": f"{uuid.uuid4()}",
                        "title": "Dummy Assignment",
                        "course": None,
                        "course_title": "Dummy Assignment Title",
                        "topic": None,
                        "status": "PENDING",
                        "due_date": timezone.now(),
                        "score": 95,
                        "total_points": 100,
                        "grade_letter": "A+",
                        "remaining_attempts": 2,
                    }
                ],
                response_only=True,
                status_codes=[200],
            ),
        ],
    ),
    create=extend_schema(
        tags=["Assignments"],
        summary="Create a new assignment synchronously",
        description="""Create a new assignment by providing the assignment details in text format.
        The system will analyze the text and extract structured assignment data.

        `content`: This will contain the json content from TipTap
        """,
        request=AssignmentTextSerializer,
        responses={
            202: OpenApiResponse(
                response=AssignmentListSerializer,
                description="Assignment created successfully",
            ),
            400: OpenApiResponse(
                description="Invalid input. Missing required fields or invalid data format"
            ),
        },
    ),
    retrieve=extend_schema(
        tags=["Assignments"],
        summary="Retrieve an assignment",
        responses={
            200: AssignmentDetailSerializer,  # Use the standard serializer
        },
        examples=[
            OpenApiExample(
                "Teacher Detail View",
                value={
                    "id": f"{uuid.uuid4()}",
                    "title": "Dummy Assignment",
                    "course": None,
                    "topic": None,
                    "status": "PENDING",
                    "raw_input": "Dummy Content",
                    "created_at": timezone.now(),
                    "due_date": timezone.now(),
                    "auto_grade_on_due_date": True,
                    "extraction_confidence": 90,
                    "assignment_type": "ESSAY",
                    "total_points": 100,
                    "question_count": 1,
                    "student_submissions": [],
                    "scheduled_grading_at": None,
                    "grading_task_name": None,
                    "is_grading_scheduled": False,
                },
                response_only=True,
                status_codes=[200],
            ),
            OpenApiExample(
                "Student Detail View",
                value={
                    "id": f"{uuid.uuid4()}",
                    "title": "Dummy Assignment",
                    "course": None,
                    "course_title": "Dummy Assignment Title",
                    "topic": None,
                    "status": "PENDING",
                    "due_date": timezone.now(),
                    "score": 95,
                    "total_points": 100,
                    "grade_letter": "A+",
                    "remaining_attempts": 2,
                    "student_submission_id": f"{uuid.uuid4()}",
                    "performance_summary": "Great job on this assignment!",
                    "assignment_raw_input": {},
                    "student_submission_raw_input": {},
                },
                response_only=True,
                status_codes=[200],
            ),
        ],
    ),
    partial_update=extend_schema(
        tags=["Assignments"],
        summary="Update an assignment",
        description="Update an existing assignment.",
        request=AssignmentTextSerializer,
        responses={
            200: AssignmentListSerializer,
            400: OpenApiResponse(description="Invalid input"),
            # 401: OpenApiResponse(description="Authentication credentials were not provided"),
            # 403: OpenApiResponse(description="You do not have permission to perform this action"),
            404: OpenApiResponse(description="Assignment not found"),
        },
    ),
    destroy=extend_schema(
        tags=["Assignments"],
        summary="Delete an assignment",
        description="Delete an assignment by ID. This action cannot be undone.",
        responses={
            204: OpenApiResponse(description="Assignment deleted successfully"),
            # 401: OpenApiResponse(description="Authentication credentials were not provided"),
            # 403: OpenApiResponse(description="You do not have permission to perform this action"),
            404: OpenApiResponse(description="Assignment not found"),
        },
    ),
)
class AssignmentViewSet(UserCacheMixin, viewsets.ModelViewSet):
    """
    API endpoint for managing assignments.

    Provides CRUD operations for assignments including:
    - List all assignments
    - Create new assignments
    - Retrieve specific assignments
    - Update assignments
    - Delete assignments

    Assignments can contain multiple questions with various types (objective, essay, etc.)
    and are used to assess student knowledge.
    """

    queryset = Assignment.objects.all()
    serializer_class = AssignmentSerializer
    permission_classes = (IsAuthenticated, IsTeacherOrReadOnly)
    pagination_class = StandardPageNumberPagination
    http_method_names = ["get", "head", "post", "delete", "patch", "options"]
    generation_history_message_limit = 12

    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]

    filterset_fields = ["course", "status", "assignment_type", "course__session"]
    search_fields = ["title", "instructions"]
    ordering_fields = ["title", "created_at", "due_date"]

    def get_queryset(self):
        user = self.request.user

        if user.user_type == UserTypes.TEACHER:
            return Assignment.objects.filter(course__teacher=user)
        elif user.user_type == UserTypes.STUDENT:
            return Assignment.objects.filter(
                course__enrollments__student=user, status=AssignmentStatus.PUBLISHED
            )
        else:
            return Assignment.objects.none()

    def get_serializer_class(self):
        user = self.request.user
        is_student = hasattr(user, "user_type") and user.user_type == UserTypes.STUDENT

        if self.action == "list":
            if is_student:
                return AssignmentListStudentSerializer
            return AssignmentListSerializer
        if self.action == "retrieve":
            if is_student:
                return AssignmentDetailStudentSerializer
            return AssignmentDetailSerializer
        if self.request.method in ["POST", "PUT", "PATCH"]:
            return AssignmentTextSerializer
        return super().get_serializer_class()

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        raw_input = serializer.validated_data.get("raw_input")
        course = serializer.validated_data.get("course")
        topic = serializer.validated_data.get("topic")
        title = serializer.validated_data.get("title")
        due_date = serializer.validated_data.get("due_date")
        auto_grade_on_due_date = serializer.validated_data.get("auto_grade_on_due_date")
        assignment_status = serializer.validated_data.get("status")

        raw_input_hash = hashlib.sha256(raw_input.encode("utf-8")).hexdigest()

        assignment = Assignment.objects.create(
            topic=topic,
            course=course,
            raw_input=raw_input,
            title=title,
            raw_input_hash=raw_input_hash,
            status=assignment_status,
            due_date=due_date,
            auto_grade_on_due_date=auto_grade_on_due_date,
        )

        text = f"""
        Analyze the text of an educational assignment and return a valid JSON

        ### Assignment Details
        {raw_input}

        ### End of Assignment Details

        IMPORTANT: Return only valid JSON matching the required structure.
        Do not include any explanatory text before or after the JSON
        """

        content = [{"type": "text", "text": text, "raw_input": raw_input}]

        assignment = AssignmentProcessingService.update_assignment_from_extraction(
            request.user,
            assignment,
            content,
            raw_input=raw_input,
            keep_existing_title=True,
        )

        serializer = AssignmentListSerializer(assignment)
        return Response(serializer.data, status=status.HTTP_202_ACCEPTED)

    @extend_schema(
        tags=["Assignments"],
        summary="Create an assignment asynchronously",
        description="Create an assignment asynchronously using background task.",
        responses={
            202: OpenApiResponse(
                response=AssignmentCreateResponseSerializer,
                description="Assignment creation and extraction task successfully started",
            ),
            400: OpenApiResponse(
                description="Invalid input. Missing required fields or invalid data format"
            ),
            500: OpenApiResponse(description="Internal server error"),
        },
    )
    # @require_ai_access
    @action(
        detail=False, methods=["post"], url_path="create-async", url_name="create-async"
    )
    @transaction.atomic
    def create_async(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        raw_input = serializer.validated_data.get("raw_input")
        course = serializer.validated_data.get("course")
        topic = serializer.validated_data.get("topic")
        title = serializer.validated_data.get("title")
        due_date = serializer.validated_data.get("due_date")
        auto_grade_on_due_date = serializer.validated_data.get("auto_grade_on_due_date")
        assignment_status = serializer.validated_data.get("status")

        raw_input_hash = hashlib.sha256(raw_input.encode("utf-8")).hexdigest()

        assignment = Assignment.objects.create(
            topic=topic,
            course=course,
            raw_input=raw_input,
            title=title,
            raw_input_hash=raw_input_hash,
            status=assignment_status,
            due_date=due_date,
            auto_grade_on_due_date=auto_grade_on_due_date,
        )

        text = f"""
        Analyze the text of an educational assignment and return a valid JSON

        ### Assignment Details
        {raw_input}

        ### End of Assignment Details

        IMPORTANT: Return only valid JSON matching the required structure.
        Do not include any explanatory text before or after the JSON
        """

        content = [{"type": "text", "text": text, "raw_input": raw_input}]

        processing_task = create_processing_task(
            requested_by=request.user,
            task_type=BackgroundTaskType.ASSIGNMENT_EXTRACTION,
            assignment=assignment,
            meta={"step": "Queued for assignment extraction"},
        )
        task = launch_processing_task(
            extract_assignment_background_task,
            processing_task,
            str(request.user.id),
            str(assignment.id),
            content,
            raw_input=raw_input,
            keep_existing_title=True,
        )

        data = {
            "assignment_id": assignment.id,
            "task_id": task.id,
            "message": "Assignment extraction started",
        }

        serializer = AssignmentCreateResponseSerializer(data)

        return Response(serializer.data, status=status.HTTP_202_ACCEPTED)

    def normalize(self, data):
        return json.loads(json.dumps(data, sort_keys=True))

    def detect_ai_assignment_override(self, assignment, updated_data):
        if not assignment.ai_generated:
            return

        ai_snapshot = self.normalize(assignment.ai_raw_payload)

        teacher_version = self.normalizer(
            {
                "title": updated_data.get("title"),
                "instructions": updated_data.get("instructions"),
                "questions": updated_data["questions"],
            }
        )

        if ai_snapshot != teacher_version:
            assignment.was_overridden = True
            assignment.overridden_at = timezone.now()

    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(
            instance=instance, data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        raw_input = serializer.validated_data.get("raw_input")
        topic = serializer.validated_data.get("topic")

        if raw_input:
            if not HasCreditBalance().has_permission(request, self):
                raise PermissionDenied(
                    "Insufficient credits to re-process this assignment edit."
                )

            text = f"""
            Analyze the text of an educational assignment and return a valid JSON

            ### Assignment Details
            {raw_input}

            ### End of Assignment Details

            IMPORTANT: Return only valid JSON matching the required structure.
            Do not include any explanatory text before or after the JSON
            """

            content = [{"type": "text", "text": text}]

            instance = AssignmentProcessingService.update_assignment_from_extraction(
                request.user,
                instance,
                content,
                topic=topic,
                raw_input=raw_input,
            )
        else:
            instance.title = serializer.validated_data.get("title", instance.title)
            instance.course = serializer.validated_data.get("course", instance.course)
            instance.topic = serializer.validated_data.get("topic", instance.topic)
            instance.status = serializer.validated_data.get("status", instance.status)
            instance.due_date = serializer.validated_data.get(
                "due_date", instance.due_date
            )
            instance.auto_grade_on_due_date = serializer.validated_data.get(
                "auto_grade_on_due_date", instance.auto_grade_on_due_date
            )

            instance.save()

        serializer = AssignmentListSerializer(instance)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        tags=["Assignments"],
        summary="Update an assignment asynchronously",
        description="""Update an existing assignment asynchronously.

        If `raw_input` is provided, non-AI fields (title, status, topic, due_date, etc.)
        are saved immediately and a background task is dispatched to re-extract and
        re-structure the assignment using AI. The response returns a `task_id` that
        can be polled for completion.\n\n
        If `raw_input` is NOT provided, all fields are updated synchronously and
        the response contains the updated assignment immediately.
        """,
        request=AssignmentTextSerializer,
        responses={
            200: AssignmentListSerializer,
            202: OpenApiResponse(
                response=AssignmentCreateResponseSerializer,
                description="AI re-extraction started — task_id returned for polling",
            ),
            400: OpenApiResponse(description="Invalid input"),
            404: OpenApiResponse(description="Assignment not found"),
        },
    )
    # @require_ai_access
    @action(
        detail=True,
        methods=["patch"],
        url_path="update-async",
        url_name="update-async",
        permission_classes=[IsAuthenticated, IsTeacher, HasCreditBalance],
    )
    def update_async(self, request, pk=None, *args, **kwargs):
        """Async variant of partial_update. Saves metadata immediately, re-extracts AI content in background."""
        instance = self.get_object()
        serializer = AssignmentTextSerializer(
            instance=instance, data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)

        raw_input = serializer.validated_data.get("raw_input")
        topic = serializer.validated_data.get("topic", instance.topic)

        # Always commit the non-AI fields synchronously so the teacher
        # sees their metadata changes immediately regardless of AI status.
        instance.title = serializer.validated_data.get("title", instance.title)
        instance.course = serializer.validated_data.get("course", instance.course)
        instance.topic = topic
        instance.status = serializer.validated_data.get("status", instance.status)
        instance.due_date = serializer.validated_data.get("due_date", instance.due_date)
        instance.auto_grade_on_due_date = serializer.validated_data.get(
            "auto_grade_on_due_date", instance.auto_grade_on_due_date
        )
        instance.save()

        if raw_input:
            text = f"""
            Analyze the text of an educational assignment and return a valid JSON

            ### Assignment Details
            {raw_input}

            ### End of Assignment Details

            IMPORTANT: Return only valid JSON matching the required structure.
            Do not include any explanatory text before or after the JSON
            """
            content = [{"type": "text", "text": text}]

            processing_task = create_processing_task(
                requested_by=request.user,
                task_type=BackgroundTaskType.ASSIGNMENT_REEXTRACTION,
                assignment=instance,
                meta={"step": "Queued for assignment re-extraction"},
            )
            task = launch_processing_task(
                update_assignment_background_task,
                processing_task,
                str(request.user.id),
                str(instance.id),
                content,
                raw_input=raw_input,
                topic_id=str(topic.id) if topic else None,
            )

            data = {
                "assignment_id": instance.id,
                "task_id": task.id,
                "message": "Assignment update and re-extraction started",
            }
            return Response(
                AssignmentCreateResponseSerializer(data).data,
                status=status.HTTP_202_ACCEPTED,
            )

        # No raw_input — return updated assignment immediately
        return Response(
            AssignmentListSerializer(instance).data,
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        tags=["Assignments"],
        summary="Associate an assignment with a topic",
        description="Associate an existing assignment with a specific topic.",
        request=None,
        parameters=[
            OpenApiParameter(
                name="topic_id",
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.QUERY,
                description="The UUID of the topic to associate with the assignment",
                required=True,
            ),
        ],
        responses={
            200: TopicSerializer,
            400: OpenApiResponse(description="Invalid input"),
            404: OpenApiResponse(description="Assignment or Topic not found"),
        },
    )
    @action(
        detail=True,
        methods=["PATCH"],
        url_path="associate-topic",
        url_name="associate-topic",
    )
    def associate_topic(self, request, pk=None):
        assignment = self.get_object()
        topic_id = request.query_params.get("topic_id")

        if not topic_id:
            raise ParseError("Topic ID is required.")

        topic = get_object_or_404(Topic, id=topic_id)

        if topic.course != assignment.course:
            raise ParseError("Topic must belong to the same course as the assignment.")

        assignment.topic = topic
        assignment.save()

        serializer = TopicSerializer(topic)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        tags=["Assignments"],
        exclude=True,
        summary="Upload assignment files (images or PDFs)",
        description="This endpoint allows users to upload one or more files. "
        "The files can be either images (JPEG, PNG, etc.) or PDFs. "
        "The system will process each file based on its type.",
        request={
            "multipart/form-data": {
                "type": "object",
                "properties": {
                    "course": {
                        "type": "string",
                        "format": "uuid",
                        "description": "The UUID of the course this assignment belongs",
                    },
                    "topic": {
                        "type": "string",
                        "format": "uuid",
                        "nullable": True,
                        "description": "(Optional) The UUID of the topic this assignment belongs",
                    },
                    "assignments": {
                        "type": "array",
                        "items": {"type": "string", "format": "binary"},
                        "description": "A list of files to upload. You can select one or multiple files to upload.",
                    },
                },
            }
        },
        responses={
            201: AssignmentDetailSerializer,
            400: {
                "description": "Bad Request. No files were uploaded or invalid file data.",
                "example": {"error": "No files were uploaded."},
            },
            415: {
                "description": "Unsupported Media Type. The uploaded file format is not allowed.",
                "example": {
                    "error": "File 'unsupported.txt' has an unsupported format. Only images and PDFs are allowed."
                },
            },
        },
    )
    # @require_ai_access
    @action(
        detail=False,
        methods=["POST"],
        url_path="upload",
        url_name="upload",
        permission_classes=[IsAuthenticated, IsTeacher],
    )
    def upload_assignment(self, request, *args, **kwargs):
        course_id = request.data.get("course")
        if not course_id:
            raise ParseError("Course ID is required.")

        # Validate course exists and user has access to it
        try:
            course = get_object_or_404(Course, id=course_id, teacher=request.user)
        except (ValueError, ValidationError):
            raise ParseError(
                "Invalid Course ID format. Must be with a valid UUID"
            ) from Exception
        except Http404:
            raise NotFound(
                "Course not found or you don't have access to it."
            ) from Http404

        topic_value = request.data.get("topic", "")
        topic_id = topic_value.strip() if isinstance(topic_value, str) else None

        if topic_id:
            topic = get_object_or_404(Topic, id=topic_id)
        else:
            topic = None

        # Access files using request.FILES
        files = request.FILES.getlist("assignments")

        if not files:
            raise ParseError("No files were uploaded.")

        # results = []

        prompt_text = """
        Analyze the image of an educational assignment and return a JSON

        IMPORTANT: Return only valid JSON matching the required structure.
        Do not include any explanatory text before or after the JSON
        """

        successful = []
        failed = []

        # Processing Loop
        for uploaded_file in files:
            # Check if it's an instance of UploadedFile
            file_name = getattr(uploaded_file, "name", "unknown_file")

            if not isinstance(uploaded_file, UploadedFile):
                failed.append(
                    {
                        "file_name": file_name,
                        "error": (
                            "The uploaded file appears to be malformed or corrupted. "
                            "Please ensure it is a valid, readable file and "
                            "try uploading again."
                        ),
                    }
                )
                continue

            try:
                validate_upload_size(uploaded_file)
            except PayloadTooLarge as exc:
                failed.append({"file_name": file_name, "error": str(exc.detail)})
                continue

            content = AssignmentProcessingService.prepare_ai_content(
                uploaded_file, prompt_text
            )
            try:
                assignment_questions = (
                    AssignmentProcessingService.extract_assignment_data(
                        request.user,
                        content,
                        course=course,
                        topic=topic,
                        generate_raw_input=True,
                        upload=True,
                    )
                )

                with transaction.atomic():
                    serializer = AssignmentSerializer(data=assignment_questions)
                    serializer.is_valid(raise_exception=True)
                    assignment = serializer.save()

                successful.append(
                    {
                        "file_name": file_name,
                        "assignment": AssignmentListSerializer(assignment).data,
                    }
                )
                # results.append(assignment_questions)

            except Exception as e:
                logger.error(
                    "Failed to extract assignment from file %s", file_name, exc_info=e
                )
                failed.append(
                    {
                        "file_name": file_name,
                        "error": describe_user_error(
                            e,
                            fallback_message=(
                                "Could not extract an assignment from this "
                                "file. Please check the file format and try "
                                "again."
                            ),
                        ),
                    }
                )

        # with transaction.atomic():
        #     serializer = AssignmentSerializer(data=results, many=True)
        #     serializer.is_valid(raise_exception=True)
        #     instance = serializer.save()
        #
        #     serializer = AssignmentListSerializer(instance, many=True)
        response_data = {
            "successful": successful,
            "failed": failed,
            "summary": {
                "total": len(files),
                "successful": len(successful),
                "failed": len(failed),
            },
        }

        if successful and failed:
            return Response(response_data, status=status.HTTP_207_MULTI_STATUS)

        if successful:
            return Response(successful, status=status.HTTP_201_CREATED)

        if failed:
            return Response(failed, status=status.HTTP_400_BAD_REQUEST)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @extend_schema(
        tags=["Assignments"],
        summary="Upload assignment files (images or PDFs)",
        description="This endpoint allows users to upload one or more files. "
        "The files can be either images (JPEG, PNG, etc.) or PDFs. "
        "The system will process each file based on its type.",
        request={
            "multipart/form-data": {
                "type": "object",
                "properties": {
                    "course": {
                        "type": "string",
                        "format": "uuid",
                        "description": "The UUID of the course this assignment belongs",
                    },
                    "topic": {
                        "type": "string",
                        "format": "uuid",
                        "nullable": True,
                        "description": "(Optional) The UUID of the topic this assignment belongs",
                    },
                    "assignments": {
                        "type": "array",
                        "items": {"type": "string", "format": "binary"},
                        "description": "A list of files to upload. You can select one or multiple files to upload.",
                    },
                },
            }
        },
        responses={
            201: BatchUploadResponseSerializer,
            400: {
                "description": "Bad Request. No files were uploaded or invalid file data.",
                "example": {"error": "No files were uploaded."},
            },
            415: {
                "description": "Unsupported Media Type. The uploaded file format is not allowed.",
                "example": {
                    "error": "File 'unsupported.txt' has an unsupported format. Only images and PDFs are allowed."
                },
            },
        },
    )
    # @require_ai_access
    @action(
        detail=False,
        methods=["POST"],
        url_path="upload-async",
        url_name="upload-async",
        permission_classes=[IsAuthenticated, IsTeacher, HasCreditBalance],
    )
    def upload_assignment_async(self, request, *args, **kwargs):
        course_id = request.data.get("course")
        if not course_id:
            raise ParseError("Course ID is required.")

        # Validate course exists and user has access to it
        course = get_object_or_404(Course, id=course_id, teacher=request.user)
        # try:
        #     course = get_object_or_404(Course, id=course_id, teacher=request.user)
        # except (ValueError, ValidationError):
        #     raise ParseError(
        #         "Invalid Course ID format. Must be with a valid UUID"
        #     ) from Exception
        # except Http404:
        #     raise NotFound(
        #         "Course not found or you don't have access to it."
        #     ) from Http404

        topic_value = request.data.get("topic", "")
        topic_id = topic_value.strip() if isinstance(topic_value, str) else None

        if topic_id:
            topic = get_object_or_404(Topic, id=topic_id)
        else:
            topic = None

        # Access files using request.FILES
        files = request.FILES.getlist("assignments")

        if not files:
            raise ParseError("No files were uploaded. Please try again")

        session = BatchUploadSession.objects.create(
            teacher=request.user,
            course=course,
            task_type=BatchUploadType.ASSIGNMENT,
            total_files=len(files),
        )
        tasks_data = []
        for uploaded_file in files:
            if not isinstance(uploaded_file, UploadedFile):
                raise ParseError(
                    (
                        "The uploaded file appears to be malformed or corrupted. "
                        "Please ensure it is a valid, readable file and try "
                        "uploading again."
                    )
                )

            validate_upload_size(uploaded_file)

            prompt_text = """
            Analyze the image of an educational assignment and return a JSON

            IMPORTANT: Return only valid JSON matching the required structure.
            Do not include any explanatory text before or after the JSON
            """

            file_payload = AssignmentProcessingService.build_async_upload_payload(
                uploaded_file
            )

            processing_task = create_processing_task(
                requested_by=request.user,
                task_type=BackgroundTaskType.BATCH_ASSIGNMENT_UPLOAD,
                batch_session=session,
                file_name=uploaded_file.name,
                meta={"step": "Queued for batch assignment extraction"},
            )
            task = launch_processing_task(
                upload_assignment_async,
                processing_task,
                user_id=str(request.user.id),
                course_id=str(course.id),
                topic_id=str(topic.id) if topic else None,
                session_id=str(session.id),
                file_payload=file_payload,
                prompt_text=prompt_text,
                file_name=uploaded_file.name,
            )
            tasks_data.append({"file_name": uploaded_file.name, "task_id": task.id})

            # tasks_data.append({"file_name": uploaded_file.name, "task_id": task})

        data = {
            "session_id": session.id,
            "message": f"Batch processing started for {len(files)} files",
            "tasks": tasks_data,
        }
        serializer = BatchUploadResponseSerializer(data)
        return Response(serializer.data, status=status.HTTP_202_ACCEPTED)

    def _build_generated_assignment_draft(self, generated_assignment, course):
        assignment_data = {
            **generated_assignment,
            "course": str(course.id),
            "ai_generated": True,
        }
        assignment_html = AssignmentProcessingService.format_assignment_standard_html(
            assignment_data
        )
        assignment_data["raw_input"] = (
            AssignmentProcessingService.html_to_prosemirror_text(assignment_html)
        )
        return assignment_data

    def _compact_assignment_generation_snapshot(self, snapshot):
        if not isinstance(snapshot, dict):
            return None

        top_level_fields = [
            "title",
            "instructions",
            "total_points",
            "question_count",
            "assignment_type",
            "status",
            "due_date",
            "auto_grade_on_due_date",
            "potential_issues",
            "extraction_confidence",
        ]
        question_fields = [
            "question_number",
            "question_text",
            "question_type",
            "points",
            "blooms_level",
            "options",
            "rubric",
            "model_answer",
            "additional_notes",
        ]

        compact_snapshot = {
            field: snapshot[field]
            for field in top_level_fields
            if field in snapshot and snapshot[field] not in [None, ""]
        }

        questions = snapshot.get("questions")
        if isinstance(questions, list):
            compact_snapshot["questions"] = [
                {
                    field: question[field]
                    for field in question_fields
                    if isinstance(question, dict)
                    and field in question
                    and question[field] not in [None, ""]
                }
                for question in questions
                if isinstance(question, dict)
            ]

        return compact_snapshot

    def _build_course_context(self, course):
        """
        Compact plain-text course summary for the AI's system prompt, used
        to ground clarifying questions and topic suggestions. Topic list
        capped to avoid unbounded prompt growth for courses with hundreds
        of topics.
        """
        lines = [f"Course name: {course.name}"]

        if course.description:
            lines.append(f"Course description: {course.description}")

        topic_names = list(
            course.topics.order_by("name").values_list("name", flat=True)[:15]
        )
        if topic_names:
            lines.append(
                "Existing topics already created in this course: "
                + ", ".join(topic_names)
            )

        return "\n".join(lines)

    def _build_assignment_generation_chat_history(self, generation_session):
        previous_messages = list(
            generation_session.messages.order_by("-created_at")[
                : self.generation_history_message_limit
            ]
        )
        chat_history = []

        for message in reversed(previous_messages):
            if message.role == AssignmentGenerationRole.USER:
                chat_history.append(
                    {
                        "role": "user",
                        "content": f"Previous teacher message:\n{message.content}",
                    }
                )
                continue

            if message.role != AssignmentGenerationRole.ASSISTANT:
                continue

            metadata = message.metadata or {}
            assistant_context = {
                "assistant_reply": metadata.get("reply", ""),
            }
            compact_snapshot = self._compact_assignment_generation_snapshot(
                message.assignment_snapshot
            )

            if compact_snapshot:
                assistant_context["assignment_draft"] = compact_snapshot

            if not assistant_context["assistant_reply"] and not compact_snapshot:
                continue

            chat_history.append(
                {
                    "role": "assistant",
                    "content": (
                        "Previous assistant response from this session. "
                        "This is compact semantic context, not editor JSON:\n"
                        f"{json.dumps(assistant_context, ensure_ascii=False)}"
                    ),
                }
            )

        return chat_history

    @extend_schema(
        tags=["Assignments"],
        summary="Generate an AI assignment draft based on user prompts",
        description="""Create an AI-generated assignment draft based on user prompts.

        This does not create an Assignment record. The generated content is saved
        as an assignment-generation message draft and can be persisted later with
        the save-generated-draft endpoint.

        Request fields:
        - `prompt` (required): the teacher instruction sent to the AI
        - `session_id` (optional): provide an existing assignment-generation session ID
          to continue that conversation thread for the same course. If omitted, a new
          session is created.
        """,
        request=AssignmentGeneratorSerializer,
        responses={
            201: GeneratedAssignmentSerializer,
            400: OpenApiResponse(
                description="Bad request - invalid data",
            ),
            403: OpenApiResponse(
                description="Not authorized",
                examples=[
                    OpenApiExample(
                        name="Not authorized",
                        value={
                            "detail": "You do not have permission to submit to this assignment"
                        },
                    )
                ],
            ),
        },
    )
    # @require_ai_access
    @action(
        detail=False,
        methods=["POST"],
        url_path=r"generate/(?P<course_id>[-\w]+)",
        url_name="generate",
    )
    def generate_assignment_from_prompt(self, request, course_id, *args, **kwargs):
        """
        Generate a new assignment draft based on text prompts.

        This endpoint accepts a text prompt and generates assignment content with
        questions and answers using AI processing. The generated assignment is kept
        as an AI draft in the generation session until the teacher explicitly saves it.
        """

        # course = Course.objects.filter(id=course_id)
        course = get_object_or_404(Course, id=course_id, teacher=request.user)
        serializer = AssignmentGeneratorSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        prompt = serializer.validated_data["prompt"]
        session_id = request.data.get("session_id")
        course_context = self._build_course_context(course)

        try:
            # Only the DB writes are transactional. The AI call below is an
            # external network round trip (up to 3 retries, each up to 2
            # sequential LLM calls, plus any tool-fetch requests) - it must
            # not run inside a DB transaction, or a slow/hanging call holds
            # a connection open and locks the session/message rows for the
            # full duration.
            with transaction.atomic():
                if session_id:
                    generation_session = get_object_or_404(
                        AssignmentGenerationSession,
                        id=session_id,
                        user=request.user,
                        course=course,
                    )
                else:
                    generation_session = AssignmentGenerationSession.objects.create(
                        user=request.user,
                        course=course,
                        title=prompt[:80],
                    )

                chat_history = self._build_assignment_generation_chat_history(
                    generation_session
                )

                user_message = AssignmentGenerationMessage.objects.create(
                    session=generation_session,
                    role=AssignmentGenerationRole.USER,
                    content=prompt,
                )

            generated_assignment = (
                ai_processor.generate_assignment_from_prompt_with_retry(
                    request.user,
                    prompt,
                    max_retries=3,
                    chat_history=chat_history,
                    course_context=course_context,
                )
            )

            # self_assessment now carries much richer, teacher-facing HTML
            # (clarifying questions / topic suggestions) than the original
            # reflection-only field this was designed for. Sanitize once
            # here so every downstream read (both branches below, message
            # storage, and the API response) is already safe - same
            # AssignmentProcessingService sanitizer boundary already
            # applied to title/instructions/question_text.
            generated_assignment["self_assessment"] = (
                AssignmentProcessingService.sanitize_ai_html(
                    generated_assignment.get("self_assessment", "")
                )
            )

            # Trust the flag when the model sets it - but a real assignment
            # can never have zero questions (AssignmentSerializer requires
            # min_length=1 below), so an empty "questions" list is always a
            # clarification-shaped response even if the model forgot to
            # also set needs_clarification. Without this OR, that mismatch
            # used to fall through to draft-building and 500 on
            # AssignmentSerializer validation instead of returning the
            # clarification turn it clearly intended.
            needs_clarification = bool(
                generated_assignment.get("needs_clarification")
            ) or not generated_assignment.get("questions")

            if needs_clarification:
                if not generated_assignment.get("needs_clarification"):
                    logger.warning(
                        "Assignment generation returned an empty-questions "
                        "payload without needs_clarification set - "
                        "treating as a clarification turn anyway."
                    )

                reply = generated_assignment.get("self_assessment") or (
                    "<p>Could you share a bit more about what you'd like "
                    "this assignment to cover?</p>"
                )

                with transaction.atomic():
                    assistant_message = AssignmentGenerationMessage.objects.create(
                        session=generation_session,
                        role=AssignmentGenerationRole.ASSISTANT,
                        content=reply,
                        assignment_snapshot=None,
                        metadata={
                            "source": "generate_assignment_from_prompt",
                            "user_message_id": str(user_message.id),
                            "reply": reply,
                            "draft_status": "NEEDS_CLARIFICATION",
                        },
                    )

                data = {
                    "content": "",
                    "reply": reply,
                    "assignment_id": None,
                    "session_id": str(generation_session.id),
                    "message_id": str(assistant_message.id),
                    "is_draft": False,
                    "needs_clarification": True,
                }

                return Response(data, status=status.HTTP_201_CREATED)

            assignment_data = self._build_generated_assignment_draft(
                generated_assignment, course
            )

            draft_serializer = AssignmentSerializer(data=assignment_data)
            draft_serializer.is_valid(raise_exception=True)

            with transaction.atomic():
                assistant_message = AssignmentGenerationMessage.objects.create(
                    session=generation_session,
                    role=AssignmentGenerationRole.ASSISTANT,
                    content=assignment_data.get("raw_input", ""),
                    assignment_snapshot=assignment_data,
                    metadata={
                        "source": "generate_assignment_from_prompt",
                        "user_message_id": str(user_message.id),
                        "reply": generated_assignment.get("self_assessment", ""),
                        "draft_status": "AI_DRAFT",
                    },
                )

            data = {
                "content": assignment_data.get("raw_input", ""),
                "reply": generated_assignment.get("self_assessment", ""),
                "assignment_id": None,
                "session_id": str(generation_session.id),
                "message_id": str(assistant_message.id),
                "is_draft": True,
                "needs_clarification": False,
            }

            return Response(data, status=status.HTTP_201_CREATED)
        except InsufficientCreditsError as e:
            logger.warning(
                "Assignment generation blocked by insufficient credits for user %s: %s",
                request.user.id,
                e,
            )
            return Response(
                {
                    "error": describe_user_error(
                        e,
                        fallback_message="Refill your wallet to continue generating assignments.",
                    )
                },
                status=status.HTTP_402_PAYMENT_REQUIRED,
            )
        except AIFeatureNotAvailableError as e:
            logger.warning(
                "Assignment generation denied by plan/tier for user %s: %s",
                request.user.id,
                e,
            )
            return Response(
                {
                    "error": describe_user_error(
                        e,
                        fallback_message=(
                            "AI assignment generation isn't available on your current plan."
                        ),
                    )
                },
                status=status.HTTP_403_FORBIDDEN,
            )
        except Http404:
            return Response(
                {
                    "error": (
                        "That generation session no longer exists. Start a "
                        "new session."
                    )
                },
                status=status.HTTP_404_NOT_FOUND,
            )
        except Exception as e:
            logger.error("Failed to generate assignment from prompt", exc_info=e)
            return Response(
                {
                    "error": describe_user_error(
                        e,
                        fallback_message=(
                            "We couldn't generate an assignment right now. "
                            "Please try again in a moment."
                        ),
                    )
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @extend_schema(
        tags=["Assignments"],
        summary="Save an AI-generated assignment draft",
        description="""Persist an AI-generated draft as a real Assignment.

        Use the `message_id` returned from the generate endpoint. This endpoint is
        idempotent: if the draft was already saved, it returns the existing
        Assignment instead of creating a duplicate.

        The fields in the body are all optional, if omitted, the AI will use the
        values from the generated assignment or you can change them here
        """,
        request=SaveGeneratedAssignmentDraftSerializer,
        responses={
            201: AssignmentListSerializer,
            200: AssignmentListSerializer,
            400: OpenApiResponse(description="Invalid or non-assignment draft"),
            404: OpenApiResponse(description="Draft not found"),
        },
    )
    # @require_ai_access
    @action(
        detail=False,
        methods=["POST"],
        url_path=r"generated-drafts/(?P<message_id>[-\w]+)/save",
        url_name="save-generated-draft",
    )
    def save_generated_assignment_draft(self, request, message_id, *args, **kwargs):
        """
        Save a generated assistant message draft as a real Assignment.
        """

        with transaction.atomic():
            draft_message = get_object_or_404(
                AssignmentGenerationMessage.objects.select_for_update()
                .select_related("session__course", "session__user")
                .filter(
                    id=message_id,
                    session__user=request.user,
                    session__course__teacher=request.user,
                    role=AssignmentGenerationRole.ASSISTANT,
                )
            )

            if draft_message.assignment_id:
                serializer = AssignmentListSerializer(draft_message.assignment)
                return Response(serializer.data, status=status.HTTP_200_OK)

            metadata = draft_message.metadata or {}
            if metadata.get("draft_status") != "AI_DRAFT":
                raise ParseError("This message is not an unsaved AI assignment draft.")

            if not draft_message.assignment_snapshot:
                raise ParseError("Draft assignment content is missing.")

            course = draft_message.session.course
            save_serializer = SaveGeneratedAssignmentDraftSerializer(
                data=request.data,
                context={"course": course},
            )
            save_serializer.is_valid(raise_exception=True)

            assignment_data = dict(draft_message.assignment_snapshot)
            assignment_data["course"] = str(course.id)

            for field, value in save_serializer.validated_data.items():
                if field == "topic":
                    assignment_data[field] = str(value.id) if value else None
                elif field == "due_date":
                    assignment_data[field] = value.isoformat() if value else None
                else:
                    assignment_data[field] = value

            assignment_html = (
                AssignmentProcessingService.format_assignment_standard_html(
                    assignment_data
                )
            )
            assignment_data["raw_input"] = (
                AssignmentProcessingService.html_to_prosemirror_text(assignment_html)
            )

            assignment_serializer = AssignmentSerializer(data=assignment_data)
            assignment_serializer.is_valid(raise_exception=True)
            assignment = assignment_serializer.save()

            draft_message.assignment = assignment
            draft_message.assignment_snapshot = assignment_data
            draft_message.metadata = {
                **metadata,
                "draft_status": "SAVED",
                "assignment_id": str(assignment.id),
                "saved_at": timezone.now().isoformat(),
            }
            draft_message.save(
                update_fields=["assignment", "assignment_snapshot", "metadata"]
            )

        serializer = AssignmentListSerializer(assignment)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @extend_schema(
        tags=["Assignments"],
        request=None,
        responses={
            200: OpenApiResponse(
                response=BatchUploadResponseSerializer,
                description="Grading of submissions started",
            )
        },
    )
    # @require_ai_access
    @action(
        detail=True,
        methods=["POST"],
        url_path=r"grade-all",
        url_name="grade-all",
        permission_classes=[IsAuthenticated, IsTeacher, HasCreditBalance],
    )
    def grade_all_submission(self, request, pk=None):

        assignment = self.get_object()

        # Get only ungraded submissions (skip already graded ones)
        ungraded_submissions = assignment.submissions.filter(Q(graded_at__isnull=True))

        if not ungraded_submissions.exists():
            raise ParseError("No ungraded submissons to process")

        session = BatchUploadSession.objects.create(
            teacher=request.user,
            course=assignment.course,
            task_type=BatchUploadType.GRADE,
            total_files=ungraded_submissions.count(),
        )
        tasks_data = []

        for submission in ungraded_submissions:
            processing_task = create_processing_task(
                requested_by=request.user,
                task_type=BackgroundTaskType.BATCH_SUBMISSION_GRADING,
                batch_session=session,
                assignment=assignment,
                submission=submission,
                file_name=f"Submission for {submission.student.get_full_name()}",
                meta={"step": "Queued for batch grading"},
            )
            task = launch_processing_task(
                grade_engine_async,
                processing_task,
                str(request.user.id),
                str(submission.id),
                batch_id=session.id,
            )
            tasks_data.append(
                {
                    "file_name": f"Submission for {submission.student.get_full_name()}",
                    "task_id": task.id,
                }
            )

        data = {
            "session_id": session.id,
            "message": f"Batch processing started for {ungraded_submissions.count()} submissions",
            "tasks": tasks_data,
        }

        serializer = BatchUploadResponseSerializer(data)

        return Response(serializer.data, status=status.HTTP_202_ACCEPTED)

    @extend_schema(
        tags=["Assignments"],
        request=ScheduleGradingSerializer,
        responses={200: ScheduledGradingResponseSerializer},
    )
    # @require_ai_access
    @action(
        detail=True,
        methods=["POST"],
        permission_classes=[IsAuthenticated, IsTeacher, HasCreditBalance],
    )
    def schedule_grade_all_submission(self, request, pk=None):
        assignment = self.get_object()

        serializer = ScheduleGradingSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        scheduled_time = serializer.validated_data["schedule_time"]

        if scheduled_time <= timezone.now():
            raise ParseError("Scheduled time cannot be in the past")

        # Cleanup existing task if it exists
        if assignment.grading_task_name:
            PeriodicTask.objects.filter(name=assignment.grading_task_name).delete()

        clocked_schedule, _ = ClockedSchedule.objects.get_or_create(
            clocked_time=scheduled_time,
        )

        task_name = f"grade-batch-{assignment.id}.{uuid.uuid4()}"

        periodic_task = PeriodicTask.objects.create(
            name=task_name,
            task="assignments.tasks.grade_batch_async",
            clocked=clocked_schedule,
            one_off=True,
            enabled=True,
            args=json.dumps([str(request.user.id), str(assignment.id)]),
            kwargs=json.dumps({}),
        )

        assignment.scheduled_grading_at = scheduled_time
        assignment.grading_task_name = task_name
        assignment.save(update_fields=["scheduled_grading_at", "grading_task_name"])

        data = {
            "period_task_id": periodic_task.id,
            "task_name": periodic_task.name,
            "scheduled_time": scheduled_time,
            "message": "Batch grading scheduled successfully",
        }

        serializer = ScheduledGradingResponseSerializer(data)

        return Response(serializer.data, status=status.HTTP_202_ACCEPTED)

    @extend_schema(
        tags=["Assignments"],
        summary="Publish all graded submissions for an assignment",
        description="Release grades and feedback for all submissions that have been graded. "
        "Ungraded submissions are ignored.",
        responses={200: PublishAllGradesResponseSerializer},
    )
    @action(
        detail=True,
        methods=["POST"],
        permission_classes=[IsAuthenticated, IsTeacher],
        url_path="publish-all-grades",
    )
    def publish_all_grades(self, request, pk=None):
        # Local import: students.services reaches back into assignments
        # (models/tasks), so importing it at module level here would create
        # an import cycle.
        from students.services import notify_student_of_graded_submission
        from students.signals import clear_student_submission_cache

        assignment = self.get_object()

        # Publishable = grading actually finished: BOTH graded_at AND a
        # score, matching the single-submission publish endpoint. The old
        # OR let half-graded rows (a failed run that set one but not the
        # other) be published to students.
        graded_submissions = assignment.submissions.filter(
            graded_at__isnull=False, score__isnull=False
        )

        total_graded = graded_submissions.count()
        if total_graded == 0:
            return Response(
                {"message": "No graded submissions found to publish."},
                status=status.HTTP_200_OK,
            )

        # Snapshot who is being published for the FIRST time before the
        # bulk write, so we can notify exactly those students. .update()
        # bypasses post_save, so unlike the single-publish endpoint nothing
        # else would notify them or invalidate caches.
        newly_published = list(graded_submissions.filter(is_published=False))
        updated_count = graded_submissions.update(is_published=True)

        for submission in newly_published:
            # The snapshot was taken before the bulk write, so the in-memory
            # instances still say is_published=False — and the notifier
            # (correctly) refuses to email about an unpublished grade.
            submission.is_published = True
            try:
                notify_student_of_graded_submission(submission)
            except Exception:
                logger.exception(
                    "Failed to notify student of published grade",
                    extra={"submission_id": str(submission.id)},
                )

        # .update() skips post_save, so fire the submission cache
        # invalidation once for the whole batch.
        if newly_published:
            clear_student_submission_cache(sender=None, instance=newly_published[0])

        total_submissions = assignment.submissions.count()
        ungraded_count = total_submissions - total_graded

        data = {
            "message": f"Successfully published {updated_count} submissions.",
            "total_graded": total_graded,
            "ungraded_count": ungraded_count,
        }

        serializer = PublishAllGradesResponseSerializer(data)

        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        tags=["Assignments"],
        summary="Download assignment as PDF",
        description=(
            "Generates a formatted PDF of the assignment. "
            "By default (no query param), the student version is returned (rubrics and model answers omitted). "
            "Add `?view=teacher` to get the teacher version (includes all content). "
            "Teacher version is restricted to the course teacher only."
        ),
        parameters=[
            OpenApiParameter(
                name="view",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description=(
                    'Set to "teacher" to get the teacher-facing version. '
                    "Omit or use any other value for student version."
                ),
                required=False,
                default="student",
                enum=["teacher", "student"],
            )
        ],
        responses={
            200: OpenApiResponse(
                description="PDF file",
                response=OpenApiTypes.BINARY,
            ),
            400: OpenApiResponse(description="Assignment has no questions"),
            403: OpenApiResponse(description="Forbidden (teacher version only)"),
            404: OpenApiResponse(description="Assignment not found"),
            500: OpenApiResponse(description="PDF generation failed"),
        },
    )
    @action(detail=True, methods=["get"], url_path="download-pdf")
    def download_pdf(self, request, pk=None):
        assignment = self.get_object()

        if not assignment.questions:
            return Response(
                {"error": "This assignment has no questions to display."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Determine which view to render
        view_param = request.query_params.get("view", "student").lower().strip()
        include_rubric = view_param == "teacher"

        # Permission enforcement for teacher view
        if include_rubric:
            # Only the teacher who owns the course can see the teacher viersion
            if assignment.course.teacher != request.user:
                raise PermissionDenied(
                    "Only the course teacher can download the teacher version."
                )
        else:
            # Student-facing version: Student can only download published assignments
            if request.user.user_type == UserTypes.STUDENT:
                if assignment.status != AssignmentStatus.PUBLISHED:
                    raise PermissionDenied(
                        "You can only download published assignments."
                    )

        # Served from cache only *after* the permission checks above, so a
        # hit can never hand someone a PDF they aren't allowed to see. The
        # early return skips the whole HTML-assembly pipeline below, not
        # just the Chromium render.
        view_type = "teacher" if include_rubric else "student"
        cached_pdf = get_cached_pdf(assignment, view_type)
        if cached_pdf is not None:
            return self._assignment_pdf_response(assignment, cached_pdf)

        # Prepare data for the assignment (common to both views)
        data = {
            "title": assignment.title,
            "instructions": assignment.instructions,
            "total_points": assignment.total_points,
            "due_date": (
                assignment.due_date.isoformat() if assignment.due_date else None
            ),
            "questions": assignment.questions,
        }

        # Generate the assignment HTML without hidden teacher content.
        # include_document_header=False: the PDF template below already
        # renders its own title/instructions/meta header, so the shared
        # formatter shouldn't render them a second time.
        html_body = AssignmentProcessingService.format_assignment_standard_html(
            data, include_rubric=include_rubric, include_document_header=False
        )

        # Extract course and teacher info. Escaped before embedding below:
        # under the previous WeasyPrint pipeline a stray "<script>" or
        # "onerror=" here was inert (WeasyPrint has no JS engine at all),
        # but Chromium actually executes JavaScript while rendering this
        # document to PDF - the same raw interpolation that was harmless
        # before is a real script-injection path now, so every value that
        # isn't already known-safe (a server-formatted date, a plain int)
        # needs escaping here, matching what format_assignment_standard_html
        # already does for the fields it renders itself.
        course_name = escape_html(
            assignment.course.name if assignment.course else "Course"
        )
        teacher_name = escape_html(
            assignment.course.teacher.get_full_name()
            if assignment.course and assignment.course.teacher
            else "Instructor"
        )

        due_date_str = (
            assignment.due_date.strftime("%B %d, %Y at %I:%M %p")
            if assignment.due_date
            else "Not set"
        )
        display_title = escape_html(
            _strip_html_from_title(assignment.title) or "Assignment"
        )
        # This endpoint calls format_assignment_standard_html with
        # include_document_header=False specifically so it can render its
        # own instructions box below instead - but that also means the
        # shared formatter's own sanitize_ai_html(instructions) call never
        # runs against the version rendered here. Assignment.instructions
        # is stored as whatever raw HTML the AI/extraction pipeline
        # produced (see AssignmentProcessingService.format_assignment_standard_html,
        # which sanitizes it lazily at render time, not at write time), so
        # it must be sanitized here too before being embedded.
        sanitized_instructions = AssignmentProcessingService.sanitize_ai_html(
            assignment.instructions or ""
        )
        instructions_html = (
            f'<div class="instructions-box"><strong>Instructions:</strong> '
            f"{sanitized_instructions}</div>"
            if sanitized_instructions
            else ""
        )

        # Build the full HTML with enhanced styling
        full_html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <title>{display_title}</title>
            <style>
                /* --- Page setup ---
                   Editorial layout patterned after the ground-truth
                   benchmark PDFs (ai_processor/benchmark/render.py):
                   quiet serif typesetting instead of a dashboard look.
                   Running headers/footers (title top-center, page count
                   bottom-center, brand mark bottom-right) are rendered via
                   Chromium's header_template/footer_template PDF options
                   (see download_pdf below), not CSS @page margin boxes -
                   Chromium's print-to-PDF doesn't support those. Page
                   margins are likewise set via page.pdf()'s margin option
                   rather than here, so they stay in sync with the space
                   those templates need. */
                @page {{
                    size: A4;
                }}

                /* --- Global styles --- */
                body {{
                    font-family: 'Georgia', 'Times New Roman', serif;
                    margin: 0;
                    padding: 0;
                    line-height: 1.65;
                    color: #20242b;
                    background: #ffffff;
                    font-size: 11.5pt;
                }}

                .container {{
                    max-width: 100%;
                }}

                /* --- Title block --- */
                .assignment-header {{
                    text-align: center;
                    margin-bottom: 28px;
                    padding-bottom: 18px;
                    border-bottom: 3px double #1a3a5c;
                }}
                .assignment-header .course-name {{
                    font-size: 10.5pt;
                    font-weight: 400;
                    letter-spacing: 1.5px;
                    text-transform: uppercase;
                    color: #96895f;
                    margin-bottom: 6px;
                }}
                .assignment-header .assignment-title {{
                    font-size: 25pt;
                    font-weight: 700;
                    color: #1a3a5c;
                    margin: 6px 0 10px 0;
                }}
                .assignment-header .meta {{
                    font-size: 10.5pt;
                    color: #5a6472;
                    font-style: italic;
                }}
                .assignment-header .meta span:not(:last-child)::after {{
                    content: " \\00b7 ";
                    font-style: normal;
                    color: #b5ab8f;
                }}

                /* --- Instructions --- */
                .instructions-box {{
                    background: #f7f6f2;
                    border-left: 3px solid #1a3a5c;
                    padding: 14px 20px;
                    margin-bottom: 28px;
                    font-size: 11pt;
                }}
                .instructions-box strong {{
                    color: #1a3a5c;
                }}

                /* --- Section heading (services.py emits "Assignment
                   Questions" as an h2 followed by a bare hr) --- */
                h2 {{
                    font-size: 13.5pt;
                    color: #1a3a5c;
                    text-transform: uppercase;
                    letter-spacing: 1px;
                    border-bottom: 1px solid #ddd8c8;
                    padding-bottom: 6px;
                    margin: 0 0 18px;
                }}
                h2 + hr {{
                    display: none;
                }}

                /* --- Questions ---
                   services.py renders each question as a plain div with
                   inline styles (shared with the ProseMirror editor
                   pipeline), so styling here targets the tags it
                   actually emits rather than card classes. */
                strong {{
                    color: #1a3a5c;
                }}
                .container > div {{
                    page-break-inside: avoid;
                }}

                /* Images */
                img {{
                    max-width: 100%;
                    height: auto;
                    display: block;
                    margin: 12px auto;
                    border: 1px solid #ddd8c8;
                }}
                /* Tables (rubric, etc.) */
                table {{
                    border-collapse: collapse;
                    width: 100%;
                    margin: 14px 0;
                    font-size: 10.5pt;
                }}
                th, td {{
                    border: 1px solid #ddd8c8;
                    padding: 7px 10px;
                    text-align: left;
                    vertical-align: top;
                }}
                th {{
                    background-color: #f0eee7;
                    font-weight: 700;
                    color: #1a3a5c;
                }}
                /* Lists */
                ul, ol {{
                    padding-left: 25px;
                    margin: 10px 0;
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <!-- Header Block -->
                <div class="assignment-header">
                    <div class="course-name">{course_name}</div>
                    <div class="assignment-title">{display_title}</div>
                    <div class="meta">
                        <span>{teacher_name}</span>
                        <span>Due {due_date_str}</span>
                        <span>{assignment.total_points or 'N/A'} marks total</span>
                    </div>
                </div>

                <!-- Instructions -->
                {instructions_html}

                <!-- Questions -->
                {html_body}
            </div>
        </body>
        </html>
        """

        # Replaces the old @page @top-center/@bottom-center/@bottom-right
        # margin boxes: Chromium's print-to-PDF has no equivalent CSS
        # support, so the running title/page-count/brand mark are built as
        # Chromium's own header/footer templates instead. Padding matches
        # the page margins below so the text lines up with the body content
        # (Chromium's templates span the full page width by default,
        # ignoring left/right margins unless padding compensates).
        # class="title" is filled in by Chromium from the document's own
        # <title> tag (set above), so it never needs to be re-escaped here.
        header_template = """
        <div style="width:100%; box-sizing:border-box;
                    padding:0 2cm 5px 2cm; margin:0;
                    font-family: Georgia, 'Times New Roman', serif;
                    font-style:italic; font-size:9px; color:#7a8188;
                    text-align:center; border-bottom:1px solid #ddd8c8;">
            <span class="title"></span>
        </div>
        """
        footer_template = """
        <div style="width:100%; box-sizing:border-box;
                    padding:5px 2cm 0 2cm; margin:0;
                    font-family: Georgia, 'Times New Roman', serif;
                    font-size:8.5px; color:#7a8188;
                    border-top:1px solid #ddd8c8;
                    display:flex; align-items:center; justify-content:space-between;">
            <span style="flex:1;"></span>
            <span style="flex:1; text-align:center;">
                Page <span class="pageNumber"></span> of <span class="totalPages"></span>
            </span>
            <span style="flex:1; text-align:right; letter-spacing:0.5px; color:#b5ab8f;">
                Grade A+
            </span>
        </div>
        """

        try:
            pdf_bytes = render_html_to_pdf(
                full_html,
                header_template=header_template,
                footer_template=footer_template,
                margins={
                    "top": "2.5cm",
                    "right": "2cm",
                    "bottom": "2.2cm",
                    "left": "2cm",
                },
            )
        except Exception as e:
            logger.error("PDF generation failed", exc_info=e)
            return Response(
                {
                    "error": describe_user_error(
                        e,
                        fallback_message=(
                            "We couldn't generate the PDF for this "
                            "assignment. Please try again or contact "
                            "support if this continues."
                        ),
                    )
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        store_pdf(assignment, view_type, pdf_bytes)
        return self._assignment_pdf_response(assignment, pdf_bytes)

    @staticmethod
    def _assignment_pdf_response(assignment, pdf_bytes):
        # Sanitise filename
        safe_title = re.sub(r"[^\w\s-]", "", assignment.title or "assignment").strip()
        filename = f"{safe_title}.pdf"

        response = FileResponse(BytesIO(pdf_bytes), content_type="application/pdf")
        response["Content-Disposition"] = f"attachment; filename={filename!r}"
        return response


@extend_schema_view(
    list=extend_schema(
        tags=["Assignment Generation Sessions"],
        summary="List assignment generation sessions",
        description="Retrieve a paginated list of assignment-generation chat sessions for the current user.",
        parameters=[
            OpenApiParameter(
                name="page",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Page number for pagination",
            ),
            OpenApiParameter(
                name="page_size",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Number of results per page (max 100)",
            ),
        ],
        responses={
            200: AssignmentGenerationSessionSerializer(many=True),
            401: OpenApiResponse(
                description="Authentication credentials were not provided"
            ),
        },
    ),
    retrieve=extend_schema(
        tags=["Assignment Generation Sessions"],
        summary="Get a specific generation session",
        description="Retrieve a generation session with its ordered chat messages.",
        responses={
            200: AssignmentGenerationSessionDetailSerializer,
            404: OpenApiResponse(description="Session not found"),
        },
    ),
    destroy=extend_schema(
        tags=["Assignment Generation Sessions"],
        summary="Delete a generation session",
        description="Delete a generation session and all its related messages.",
        responses={
            200: OpenApiResponse(description="Session deleted successfully"),
            404: OpenApiResponse(description="Session not found"),
        },
    ),
)
class AssignmentGenerationSessionViewSet(UserCacheMixin, viewsets.ModelViewSet):
    """
    API endpoint for browsing assignment-generation chat sessions.

    Provides read-only access to:
    - List all sessions for the current teacher
    - Retrieve a specific session with its full prompt/response history
    """

    queryset = AssignmentGenerationSession.objects.all()
    serializer_class = AssignmentGenerationSessionSerializer
    permission_classes = (IsAuthenticated,)
    pagination_class = StandardPageNumberPagination
    http_method_names = ["get", "head", "delete", "options"]

    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ["course"]
    ordering_fields = ["created_at", "updated_at", "title"]
    ordering = ["-updated_at", "-created_at"]

    def get_queryset(self):
        return (
            AssignmentGenerationSession.objects.filter(user=self.request.user)
            .select_related("course", "user")
            .prefetch_related("messages", "messages__assignment")
        )

    def get_serializer_class(self):
        if self.action == "retrieve":
            return AssignmentGenerationSessionDetailSerializer
        return AssignmentGenerationSessionSerializer
