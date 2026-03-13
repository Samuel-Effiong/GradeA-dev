import hashlib
import json

from django.core.files.uploadedfile import UploadedFile
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.cache import cache_page
from django.views.decorators.vary import vary_on_headers
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
from rest_framework.exceptions import NotFound, ParseError, ValidationError
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from ai_processor.models import ChatMessage, ChatSession, RoleType
from ai_processor.serializers import AssignmentGeneratorSerializer
from ai_processor.services import ai_processor  # pdf_service

# from ai_processor.tools import encode_image
from classrooms.models import Course, Topic
from classrooms.permissions import IsTeacher, IsTeacherOrReadOnly
from classrooms.serializers import TopicSerializer
from students.models import StudentSubmission

# from students.serializers import StudentSubmissionSerializer
from users.models import UserTypes

from .models import Assignment, AssignmentStatus  # Rubric
from .serializers import (  # RubricSerializer,
    AssignmentCreateResponseSerializer,
    AssignmentDetailSerializer,
    AssignmentGradeAllSubmissions,
    AssignmentListSerializer,
    AssignmentSerializer,
    AssignmentTextSerializer,
    GeneratedAssignmentSerializer,
)
from .services import AssignmentProcessingService
from .tasks import (
    extract_assignment_background_task,
    grade_all_submissions,
    upload_assignment_async,
)

# from students.models import StudentSubmission


# from ai_processor.validators import AssignmentStructure

# from assignments.services import PDFService


@extend_schema_view(
    list=extend_schema(
        tags=["Assignments"],
        summary="List all assignments",
        description="Retrieve a paginated list of all assignments in the system.",
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
                description="Number of results per page",
            ),
        ],
        responses={
            200: AssignmentListSerializer(many=True),
            500: OpenApiResponse(description="Internal Server Error"),
        },
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
        description="Retrieve detailed information about a specific assignment by its ID.",
        responses={
            200: AssignmentDetailSerializer,
            404: OpenApiResponse(description="Assignment not found"),
            500: OpenApiResponse(description="Internal Server Error"),
        },
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
class AssignmentViewSet(viewsets.ModelViewSet):
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
    pagination_class = PageNumberPagination
    http_method_names = ["get", "head", "post", "delete", "patch", "options"]

    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]

    filterset_fields = ["course", "assignment_type"]
    search_fields = ["title", "instructions"]
    ordering_fields = ["title", "created_at", "due_date"]

    # def get_permissions(self):
    #     if (
    #         self.action == "create"
    #         or self.action == "destroy"
    #         or self.action == "partial_update"
    #     ):
    #         permission_classes = [IsAuthenticated, IsTeacher]
    #     else:
    #         permission_classes = [IsAuthenticated]
    #
    #     return [permission() for permission in permission_classes]

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
        if self.action == "list":
            return AssignmentListSerializer
        if self.action == "retrieve":
            return AssignmentDetailSerializer
        if self.request.method in ["POST", "PUT", "PATCH"]:
            return AssignmentTextSerializer
        return super().get_serializer_class()

    @method_decorator(cache_page(60 * 3, key_prefix="assignments:list"))
    @method_decorator(vary_on_headers("Authorization"))
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @method_decorator(cache_page(60 * 3, key_prefix="assignments:detail"))
    @method_decorator(vary_on_headers("Authorization"))
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        raw_input = serializer.validated_data.get("raw_input")
        course = serializer.validated_data.get("course")
        topic = serializer.validated_data.get("topic")
        title = serializer.validated_data.get("title")

        assignment = Assignment.objects.create(
            topic=topic,
            course=course,
            raw_input=raw_input,
            title=title,
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

        raw_input_hash = hashlib.sha256(raw_input.encode("utf-8")).hexdigest()

        assignment = Assignment.objects.create(
            topic=topic,
            course=course,
            raw_input=raw_input,
            title=title,
            raw_input_hash=raw_input_hash,
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

        task = extract_assignment_background_task.delay(
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
            #
            # extraction_started_at = timezone.now()
            #
            # assignment_questions = ai_processor.extract_assignment_with_retry(
            #     request.user, content, max_retries=3
            # )
            #
            # extraction_completed_at = timezone.now()
            #
            # assignment_questions["course"] = instance.course.id
            # assignment_questions["raw_input"] = raw_input
            #
            # assignment_questions["extraction_started_at"] = extraction_started_at
            # assignment_questions["extraction_completed_at"] = extraction_completed_at
            #
            # if topic:
            #     assignment_questions["topic"] = topic.id

            # create assignment object
            # assignment_serializer = AssignmentSerializer(
            #     instance=instance, data=assignment_questions, partial=True
            # )
            # assignment_serializer.is_valid(raise_exception=True)
            # instance = assignment_serializer.save()

        else:
            instance.title = serializer.validated_data.get("title", instance.title)
            instance.course = serializer.validated_data.get("course", instance.course)
            instance.topic = serializer.validated_data.get("topic", instance.topic)
            instance.status = serializer.validated_data.get("status", instance.status)

            instance.save()

        serializer = AssignmentListSerializer(instance)
        return Response(serializer.data, status=status.HTTP_200_OK)

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
                failed.append({"file_name": file_name, "error": "Invalid file upload."})
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
                # raise ParseError(str(e)) from Exception
                failed.append({"file_name": file_name, "error": str(e)})

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
    @action(
        detail=False,
        methods=["POST"],
        url_path="upload-async",
        url_name="upload-async",
        permission_classes=[IsAuthenticated, IsTeacher],
    )
    def upload_assignment_async(self, request, *args, **kwargs):
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

        files_payload = []

        for uploaded_file in files:
            if not isinstance(uploaded_file, UploadedFile):
                raise ParseError("Invalid file upload.")

            files_payload.append(
                AssignmentProcessingService.build_async_upload_payload(uploaded_file)
            )

        task = upload_assignment_async.delay(
            user_id=str(request.user.id),
            course_id=str(course.id),
            topic_id=str(topic.id) if topic else None,
            files_payload=files_payload,
        )

        data = {
            "task_id": task.id,
            "message": "Assignment upload extraction started",
            "file_count": len(files_payload),
        }

        return Response(data, status=status.HTTP_202_ACCEPTED)

    @extend_schema(
        tags=["Assignments"],
        summary="Generate an assignment based on user prompts",
        description="""Create a new assignment based on user prompts.""",
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
    @action(
        detail=False,
        methods=["POST"],
        url_path=r"generate/(?P<course_id>[-\w]+)",
        url_name="generate",
    )
    def generate_assignment_from_prompt(self, request, course_id, *args, **kwargs):
        """
        Generate a new assignment based on text prompts.

        This endpoint accepts a text prompt and generates an assignment with questions
        and answers using AI processing.
        """

        course = Course.objects.filter(id=course_id)

        if not course.exists():
            raise NotFound("No Course found with this ID.")

        # Get all the past history of the user chats for this particular course
        chat_session, created = ChatSession.objects.get_or_create(course_id=course_id)
        chat_history = (
            ChatMessage.objects.filter(session=chat_session)
            .order_by("timestamp")
            .values_list("role", "content")
        )

        messages = [
            {"role": role, "content": content} for role, content in chat_history
        ]

        prompt = request.data.get("prompt")
        if not prompt:
            raise ParseError("Prompt is required to generate an assignment.")

        try:

            generated_assignment = (
                ai_processor.generate_assignment_from_prompt_with_retry(
                    request.user, prompt, chat_history=messages, max_retries=3
                )
            )

            # Store the new user message
            ChatMessage.objects.create(
                session=chat_session, role=RoleType.USER, content=prompt
            )

            # Store the AI's response in the chat history
            ChatMessage.objects.create(
                session=chat_session,
                role=RoleType.ASSISTANT,
                content=str(generated_assignment),
            )

            assignment_standard = (
                AssignmentProcessingService.format_assignment_standard_html(
                    generated_assignment
                )
            )
            assignment_prosemirror_json = (
                AssignmentProcessingService.html_to_prosemirror_json(
                    assignment_standard
                )
            )

            data = {"content": assignment_prosemirror_json}

            serializer = GeneratedAssignmentSerializer(data)

            return Response(serializer.data, status=status.HTTP_201_CREATED)
        except Exception as e:
            return Response(
                {"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @extend_schema(tags=["Assignments"])
    @action(detail=True, methods=["GET"], url_path=r"grade-all", url_name="grade-all")
    def grade_all_submission(self, request, pk=None):
        assignment = self.get_object()

        submissions = assignment.submissions.all()

        task_id = None

        if not submissions.exists():
            return Response(
                {"message": "No submissions to grade"}, status=status.HTTP_200_OK
            )

        print("Assignment ID: ", Assignment.objects.filter(id=assignment.id))
        print("Submission:", StudentSubmission.objects.filter(assignment=assignment))
        task = grade_all_submissions.delay(str(assignment.id))
        task_id = task.id

        data = {
            "assignment_id": assignment.id,
            "task_id": task_id,
            "message": "AI grading started",
            "submission_count": submissions.count(),
            "status": "Processing" if task_id else "completed",
        }

        serializer = AssignmentGradeAllSubmissions(data=data)
        serializer.is_valid(raise_exception=True)

        return Response(serializer.data, status=status.HTTP_202_ACCEPTED)
