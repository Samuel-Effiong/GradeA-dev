# from django.shortcuts import render
import json
import logging
import uuid

from django.conf import settings
from django.core.cache import cache
from django.core.files.uploadedfile import UploadedFile
from django.db.models import F
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django_celery_beat.models import (  # , PeriodicTask, PeriodicTasks
    ClockedSchedule,
    PeriodicTask,
)

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

# from PIL.Image import Image
from rest_framework import filters, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotAcceptable, ParseError
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.settings import api_settings
from rest_framework.status import (
    HTTP_200_OK,
    HTTP_201_CREATED,
    HTTP_202_ACCEPTED,
    HTTP_400_BAD_REQUEST,
    HTTP_409_CONFLICT,
    HTTP_500_INTERNAL_SERVER_ERROR,
)

from ai_processor.services import ai_processor
from assignments.models import Assignment, AssignmentStatus
from assignments.serializers import (
    BatchUploadResponseSerializer,
    ScheduledGradingResponseSerializer,
    ScheduleGradingSerializer,
)
from assignments.services import AssignmentProcessingService
from assignments.tasks import (
    formatted_grade_async,
    grade_engine_async,
    upload_answers_engine_async,
)
from AutoGrader.error_messages import describe_user_error
from AutoGrader.pagination import StandardPageNumberPagination
from AutoGrader.uploads import validate_upload_size
from classrooms.permissions import IsStudent, IsTeacher, IsTeacherOrReadOnly
from users.mixins import UserCacheMixin
from users.models import CustomUser, UserTypes
from users.permissions import HasCreditBalance

from .exceptions import SubmissionGradingInProgressError
from .models import (
    BackgroundTaskType,
    BatchUploadSession,
    BatchUploadType,
    StudentSubmission,
)
from .serializers import (
    StudentListSerializer,
    StudentSubmissionDetailSerializer,
    StudentSubmissionDetailStudentVersionSerializer,
    StudentSubmissionFormattedGradeAsyncSerializer,
    StudentSubmissionGradeAsyncSerializer,
    StudentSubmissionGradeUpdateSerializer,
    StudentSubmissionListSerializer,
    StudentSubmissionSerializer,
    StudentSubmissionTeacherFeedbackSerializer,
    StudentSubmissionUpdateSerializer,
    StudentSubmissionUploadAsyncSerializer,
)
from .services import (
    _coerce_confidence,
    grade_engine,
    notify_student_of_graded_submission,
    student_submission_to_html,
    upload_answers_engine,
)
from .signals import delete_cache_patterns
from .task_tracking import create_processing_task, launch_processing_task

logger = logging.getLogger(__name__)

# from openai.types import Batch


# Create your views here.
@extend_schema_view(
    list=extend_schema(
        tags=["07 Student Submissions"],
        summary="List all student submissions",
        description="Retrieve a paginated list of all student submissions in the system.",
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
            ),
        ],
        responses={
            200: StudentSubmissionListSerializer(many=True),
            500: OpenApiResponse(description="Internal Server Error"),
        },
    ),
    create=extend_schema(
        tags=["07 Student Submissions"],
        summary="Create a new student submission",
        description="Create a new student submission with the provided details.",
        request=StudentSubmissionSerializer,
        responses={
            201: OpenApiResponse(
                response=StudentSubmissionSerializer,
                description="Assignment successfully submitted",
            ),
            400: OpenApiResponse(
                description="Invalid input. Missing required fields or invalid data format"
            ),
        },
    ),
    retrieve=extend_schema(
        tags=["07 Student Submissions"],
        summary="Retrieve a student submission",
        description="Retrieve detailed information about a specific student submission by its ID.",
        responses={
            200: StudentSubmissionDetailSerializer,
            404: OpenApiResponse(description="Student submission not found"),
            500: OpenApiResponse(description="Internal Server Error"),
        },
    ),
    partial_update=extend_schema(
        tags=["07 Student Submissions"],
        summary="Update a student submission",
        description="Update a student submission.",
        request=StudentSubmissionUpdateSerializer,
        responses={
            200: StudentSubmissionListSerializer,
            400: OpenApiResponse(description="Invalid input"),
            404: OpenApiResponse(description="Student submission not found"),
        },
    ),
    destroy=extend_schema(
        tags=["07 Student Submissions"],
        summary="Delete a student submission",
        description="Delete a student submission by ID. This action cannot be undone.",
        responses={
            204: OpenApiResponse(description="Student submission deleted successfully"),
            404: OpenApiResponse(description="Student submission not found"),
        },
    ),
)
class StudentSubmissionViewSet(UserCacheMixin, viewsets.ModelViewSet):
    queryset = StudentSubmission.objects.all()
    serializer_class = StudentSubmissionSerializer
    permission_classes = (IsAuthenticated,)
    pagination_class = StandardPageNumberPagination
    http_method_names = ["get", "head", "post", "delete", "patch", "options"]

    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]

    # grading_state lets a teacher query "what's still RUNNING / FAILED"
    # (the field is indexed for exactly this filter).
    filterset_fields = [
        "assignment",
        "grading_state",
        "is_published",
        # The teacher's review queue: ?needs_review=true lists submissions
        # where the two AI graders disagreed (indexed for this filter).
        "needs_review",
        # ...and ?review_tier=critical narrows that to the ones worth
        # opening first. Denormalised onto its own indexed column because
        # the tier lives inside review_reasons, a JSONField, which
        # django-filter cannot filter on.
        "review_tier",
    ]
    search_fields = ["student__first_name", "student__last_name"]
    # review_severity: the queue triage order —
    # ?needs_review=true&ordering=-review_severity puts critical
    # disagreements ahead of moderate ones, and orders by point gap within
    # a tier (the stored value is tier-weighted; see
    # students/services.py::_review_sort_key).
    ordering_fields = ["student__first_name", "student__last_name", "review_severity"]
    ordering = ["student__first_name"]

    def filter_queryset(self, queryset):
        queryset = super().filter_queryset(queryset)
        # Postgres sorts NULLs FIRST on a DESC ordering, so an unqualified
        # ?ordering=-review_severity returned every un-flagged submission
        # (severity NULL) ahead of the actual review queue. Force NULLs
        # last so the ordering is useful with or without needs_review=true.
        raw = self.request.query_params.get(api_settings.ORDERING_PARAM)
        if not raw:
            return queryset
        terms = [term.strip() for term in raw.split(",") if term.strip()]
        if not any(term.lstrip("-") == "review_severity" for term in terms):
            return queryset

        # Re-validate against ordering_fields: this bypasses OrderingFilter,
        # so an unvetted field name here would be an ORM injection point.
        allowed = set(self.ordering_fields)
        expressions = []
        for term in terms:
            name = term.lstrip("-")
            if name not in allowed:
                continue
            field = F(name)
            expressions.append(
                field.desc(nulls_last=True)
                if term.startswith("-")
                else field.asc(nulls_last=True)
            )
        return queryset.order_by(*expressions) if expressions else queryset

    # @method_decorator(cache_page(60 * 3, key_prefix="studentsubmissions:detail"))
    # @method_decorator(vary_on_headers("Authorization"))
    def retrieve(self, request, *args, **kwargs):
        submission = self.get_object()
        cache_key = f"studentsubmissions:user_id__{request.user.id}:instance_id__{submission.id}"

        data = cache.get(cache_key)
        if data:
            return Response(data)

        if not submission.raw_input:
            answer_html = student_submission_to_html(submission)
            submission.raw_input = AssignmentProcessingService.html_to_prosemirror_text(
                answer_html
            )
            # Queryset .update(), NOT instance.save(): this is a lazy
            # backfill of derived display data on a GET. A save() here
            # fires post_save, which recalculates the course final grade
            # and pattern-deletes every dashboard cache — a teacher merely
            # paging through submissions repeatedly flushed deployment-wide
            # caches. Nothing grade-bearing changes, so skipping signals is
            # correct, not just cheaper.
            StudentSubmission.objects.filter(pk=submission.pk).update(
                raw_input=submission.raw_input
            )

        if request.user.user_type == UserTypes.STUDENT:
            serializer = StudentSubmissionDetailStudentVersionSerializer(
                submission, context=self.get_serializer_context()
            )
        else:
            serializer = StudentSubmissionDetailSerializer(
                submission, context=self.get_serializer_context()
            )

        data = serializer.data
        cache.set(cache_key, data, getattr(settings, "CACHE_TTL", 60 * 5))

        return Response(data)

    @extend_schema(exclude=True)
    def create(self, request, *args, **kwargs):
        raise NotImplementedError("Student can only upload answers to answers")

    def get_queryset(self):
        user = self.request.user

        if user.user_type == UserTypes.STUDENT:
            return StudentSubmission.objects.filter(student=user).exclude(
                assignment__status__in=[
                    AssignmentStatus.DRAFT,
                    AssignmentStatus.UNPUBLISHED,
                ]
            )
        elif user.user_type == UserTypes.TEACHER:
            return StudentSubmission.objects.filter(assignment__course__teacher=user)
        else:
            return StudentSubmission.objects.none()

    def get_serializer_class(self):
        if self.action == "list":
            return StudentSubmissionListSerializer
        elif self.action == "retrieve":
            if self.request.user.user_type == UserTypes.STUDENT:
                return StudentSubmissionDetailStudentVersionSerializer
            return StudentSubmissionDetailSerializer
        return StudentSubmissionSerializer

    def get_permissions(self):
        """
        Custom permissions for StudentSubmissionViewSet:
        - List and Retrieve: Both Student and Teacher (Authenticated only).
        - Create, Uploads, Partial Update: Student only (Requires Credits for AI extraction).
        - Batch Upload, Grading, Feedback, Regrading: Teacher only (Requires Credits for AI tasks).
        - Destroy: Teacher only (No credits required).
        """
        if self.action in ["list", "retrieve"]:
            permission_classes = [IsAuthenticated]
        elif self.action in [
            "create",
            "upload_answers",
            "upload_answers_async",
            "update",
        ]:
            # These are student actions that (mostly) consume AI credits
            permission_classes = [IsAuthenticated, IsStudent, HasCreditBalance]
        elif self.action in [
            "batch_upload",
            "grade",
            "grade_async",
            "schedule_grade_async",
            "teacher_feedback",
            "update_grade",
        ]:
            # These are teacher actions that consume AI credits
            permission_classes = [IsAuthenticated, IsTeacher, HasCreditBalance]
        else:
            # Everything else (e.g., destroy) is teacher-only
            permission_classes = [IsAuthenticated, IsTeacher]

        return [permission() for permission in permission_classes]

    @extend_schema(
        tags=["07 Student Submissions"],
        summary="Upload answers for a student submission",
        description="Upload answers for a student submission.",
        request={
            "multipart/form-data": {
                "type": "object",
                "properties": {
                    "answer": {
                        "type": "string",
                        "format": "binary",
                        "description": "Answer file (PDF, JPEG, PNG, GIF, or WebP)",
                    }
                },
                "required": ["answer"],
            }
        },
        responses={
            201: OpenApiResponse(
                response=StudentSubmissionDetailSerializer,
                description="Answer processed successfully",
            ),
            400: OpenApiResponse(
                description="Invalid input. Missing required fields or invalid data format",
                examples=[
                    OpenApiExample(
                        name="No File",
                        value={"error": "Invalid file upload"},
                        response_only=True,
                    )
                ],
            ),
            415: OpenApiResponse(
                description="Unsupported Media Type",
                examples=[
                    OpenApiExample(
                        name="Unsupported Media Type",
                        value={
                            "error": "File 'example.txt' has an invalid format. "
                            "Only images (JPEG, PNG, GIF, WebP) and PDFs are allowed."
                        },
                        response_only=True,
                    )
                ],
            ),
            500: OpenApiResponse(
                description="Internal Server Error",
                examples=[
                    OpenApiExample(
                        name="Internal Server Error",
                        value={"error": "Internal Server Error"},
                        response_only=True,
                    )
                ],
            ),
        },
    )
    @action(
        detail=False,
        methods=["POST"],
        url_path=r"(?P<assignment_id>[-\w]+)/upload",
        url_name="upload-answers",
        permission_classes=[IsAuthenticated, IsStudent, HasCreditBalance],
    )
    def upload_answers(self, request, assignment_id=None, *args, **kwargs):
        assignment = get_object_or_404(Assignment, id=assignment_id)

        if assignment.status != AssignmentStatus.PUBLISHED:
            raise ParseError("This assignment is not currently open for submissions.")

        files = request.FILES.getlist("answer")
        if not files:
            raise ParseError("No files uploaded. Please try again.")

        if len(files) > 1:
            raise NotAcceptable(detail="Only one file can be uploaded at a time")

        uploaded_file = files[0]
        if not isinstance(uploaded_file, UploadedFile):
            raise ParseError(
                "Invalid file upload. Only images (JPEG, PNG, GIF, WebP) and PDFs are allowed."
            )

        validate_upload_size(uploaded_file)

        prompt = """
        Analyze the image of an educational assignment and return a JSON

        IMPORTANT: Return only valid JSON matching the required structure.
        Do not include any explanatory text before or after the JSON
        """

        content = AssignmentProcessingService.prepare_ai_content(uploaded_file, prompt)

        try:

            submission = upload_answers_engine(assignment, content, request.user)
            serializer = StudentSubmissionDetailSerializer(submission)

            return Response(serializer.data, status=HTTP_201_CREATED)
        except Exception as e:
            logger.error("Failed to process submission upload", exc_info=e)
            return Response(
                {
                    "error": describe_user_error(
                        e,
                        fallback_message=(
                            "We couldn't process your submission. Please "
                            "check the file and try again."
                        ),
                    )
                },
                status=HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @extend_schema(
        tags=["07 Student Submissions"],
        summary="Upload answers to an assignment asynchronously",
        description="Upload answers to an assignment asynchronously",
        request={
            "multipart/form-data": {
                "type": "object",
                "properties": {
                    "answer": {
                        "type": "string",
                        "format": "binary",
                        "description": "Answer file (PDF, JPEG, PNG, GIF, WebP)",
                    }
                },
                "required": ["answer"],
            }
        },
    )
    @action(
        detail=False,
        methods=["POST"],
        permission_classes=[IsAuthenticated, IsStudent, HasCreditBalance],
        url_path=r"(?P<assignment_id>[-\w]+)/upload-async",
        url_name="upload-async",
    )
    def upload_answers_async(self, request, assignment_id=None, *args, **kwargs):
        assignment = get_object_or_404(Assignment, id=assignment_id)

        if assignment.status != AssignmentStatus.PUBLISHED:
            raise ParseError("This assignment is not currently open for submissions.")

        files = request.FILES.getlist("answer")
        if not files:
            raise ParseError("No files uploaded. Please try again.")

        if len(files) > 1:
            raise NotAcceptable(detail="Only one file can be uploaded at a time")

        uploaded_file = files[0]
        if not isinstance(uploaded_file, UploadedFile):
            raise ParseError(
                "Invalid file upload. Only images (JPEG, PNG, GIF, WebP) and PDFs are allowed."
            )

        validate_upload_size(uploaded_file)

        prompt = """
        Analyze the image of an educational assignment and return a JSON

        IMPORTANT: Return only valid JSON matching the required structure.
        Do not include any explanatory text before or after the JSON
        """

        file_payload = AssignmentProcessingService.build_async_upload_payload(
            uploaded_file
        )

        task_id = None

        processing_task = create_processing_task(
            requested_by=request.user,
            task_type=BackgroundTaskType.ANSWER_EXTRACTION,
            assignment=assignment,
            file_name=uploaded_file.name,
            meta={"step": "Queued for answer extraction"},
        )
        task = launch_processing_task(
            upload_answers_engine_async,
            processing_task,
            str(assignment.id),
            file_payload,
            prompt,
            str(request.user.id),
        )
        task_id = task.id

        # task = upload_answers_engine_async(
        #     str(assignment.id), content, str(request.user.id)
        # )
        # task_id = task_id

        data = {"task_id": task_id, "message": "Answer Extraction Started"}

        serializer = StudentSubmissionUploadAsyncSerializer(data)

        return Response(serializer.data, status=HTTP_200_OK)

    def partial_update(self, request, *args, **kwargs):
        raw_input = request.data.get("raw_input")

        submission = self.get_object()
        assignment = submission.assignment

        if assignment.status != AssignmentStatus.PUBLISHED:
            raise ParseError("Cannot update submission for a non-published assignment.")

        try:
            assignment_context = f"""
            This is the Assignment Context to use in properly extracting the student submissions
            {assignment.questions}
            """

            prompt = """
            Analyze the content of an educational assignment that is sent to you in PROSEMIRROR FORMAT and return a JSON

            IMPORTANT: Return only valid JSON matching the required structure.
            Do not include any explanatory text before or after the JSON
            """

            content = [
                {"type": "text", "text": prompt},
                {"type": "text", "text": raw_input},
            ]

            student_submission = ai_processor.extract_answer_with_retry(
                request.user,
                content,
                assignment_context,
                assignment_model=assignment,
                max_retries=3,
            )

            if student_submission is not None:

                serializer = StudentSubmissionSerializer(
                    submission, data=student_submission, partial=True
                )
                serializer.is_valid(raise_exception=True)
                submission = serializer.save()

                answer_html = student_submission_to_html(submission)
                submission.raw_input = (
                    AssignmentProcessingService.html_to_prosemirror_text(answer_html)
                )
                # Persist the extractor's confidence - the dashboard
                # threshold-flags low-confidence extractions, which stayed 0
                # forever while this field was silently dropped here.
                submission.extraction_confidence = _coerce_confidence(
                    student_submission.get("extraction_confidence")
                )
                submission.save()

                serializer = StudentSubmissionListSerializer(submission)

                return Response(serializer.data, status=HTTP_201_CREATED)
        except Exception as e:
            logger.error("Failed to save submission update", exc_info=e)
            return Response(
                {
                    "error": describe_user_error(
                        e,
                        fallback_message=(
                            "We couldn't save your update. Please try again."
                        ),
                    )
                },
                status=HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @extend_schema(
        tags=["07 Student Submissions"],
        request=None,
        responses={
            HTTP_200_OK: StudentSubmissionDetailSerializer,
        },
    )
    @action(
        detail=True,
        methods=["POST"],
        permission_classes=[IsAuthenticated, IsTeacher, HasCreditBalance],
        url_path="grade",
    )
    def grade(self, request, pk=None):
        # get_object() runs get_queryset() (which scopes teachers to their
        # own courses) plus check_object_permissions — using the manager
        # directly here would let any teacher grade any submission by pk.
        submission = self.get_object()

        try:
            submission = grade_engine(request.user, submission)
            serializer = StudentSubmissionDetailSerializer(submission)

            return Response(serializer.data, status=HTTP_200_OK)

        except SubmissionGradingInProgressError:
            # Not a failure — another request or a redelivered Celery task
            # is already grading this submission. Surface as a distinct,
            # non-alarming status rather than a 500.
            return Response(
                {
                    "error": (
                        "This submission is already being graded. Please "
                        "wait for it to finish before trying again."
                    )
                },
                status=HTTP_409_CONFLICT,
            )

        except Exception as e:
            logger.error("Grading failed", exc_info=e)
            return Response(
                {
                    "error": describe_user_error(
                        e,
                        fallback_message=(
                            "Grading failed. Please try again — if the "
                            "problem continues, contact support."
                        ),
                    )
                },
                status=HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @extend_schema(
        tags=["07 Student Submissions"],
        request=None,
        responses={
            HTTP_200_OK: StudentSubmissionDetailSerializer,
        },
    )
    @action(
        detail=True,
        methods=["POST"],
        permission_classes=[IsAuthenticated, IsTeacher, HasCreditBalance],
        url_path="grade-async",
    )
    def grade_async(self, request, pk=None):
        submission = self.get_object()

        processing_task = create_processing_task(
            requested_by=request.user,
            task_type=BackgroundTaskType.SUBMISSION_GRADING,
            assignment=submission.assignment,
            submission=submission,
            file_name=f"Submission for {submission.student.get_full_name()}",
            meta={"step": "Queued for grading"},
        )
        task = launch_processing_task(
            grade_engine_async,
            processing_task,
            str(request.user.id),
            str(submission.id),
        )
        task_id = task.id

        data = {
            "submission_id": submission.id,
            "task_id": task_id,
            "message": "Grade engine started successfully",
        }

        serializer = StudentSubmissionGradeAsyncSerializer(data)

        return Response(serializer.data, status=HTTP_200_OK)

    @extend_schema(
        tags=["07 Student Submissions"],
        request=ScheduleGradingSerializer,
        responses={200: ScheduledGradingResponseSerializer},
    )
    @action(
        detail=True,
        methods=["POST"],
        permission_classes=[IsAuthenticated, IsTeacher, HasCreditBalance],
        url_path="schedule-grade-async",
    )
    def schedule_grade_async(self, request, pk=None):
        submission = self.get_object()

        serializer = ScheduleGradingSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        scheduled_time = serializer.validated_data["schedule_time"]

        if scheduled_time <= timezone.now():
            raise ParseError({"schedule_time": "Scheduled time must be in the future"})

        # If Due date, scheduled time must not be less than due date
        due_date = submission.assignment.due_date

        if due_date and scheduled_time <= due_date:
            raise ParseError("Scheduled time must be after the due date")

        # Cleanup existing task if it exists
        if submission.grading_task_name:
            PeriodicTask.objects.filter(name=submission.grading_task_name).delete()

        clocked_schedule, _ = ClockedSchedule.objects.get_or_create(
            clocked_time=scheduled_time
        )

        task_name = f"grade-submission-{submission.id}-{uuid.uuid4()}"

        periodic_task = PeriodicTask.objects.create(
            name=task_name,
            task="assignments.tasks.grade_engine_async",
            clocked=clocked_schedule,
            one_off=True,
            enabled=True,
            args=json.dumps([str(request.user.id), str(submission.id)]),
        )

        submission.scheduled_grading_at = scheduled_time
        submission.grading_task_name = task_name
        submission.save(update_fields=["scheduled_grading_at", "grading_task_name"])

        data = {
            "period_task_id": periodic_task.id,
            "task_name": periodic_task.name,
            "scheduled_time": scheduled_time,
            "message": "Grading scheduled successfully",
        }

        serializer = ScheduledGradingResponseSerializer(data)
        return Response(serializer.data, status=HTTP_202_ACCEPTED)

    @extend_schema(
        tags=["07 Student Submissions"],
        responses={
            HTTP_200_OK: StudentSubmissionTeacherFeedbackSerializer,
        },
    )
    @action(
        detail=True,
        methods=["GET"],
        # NOTE: this kwarg is dead — get_permissions() below overrides it and
        # actually runs this action as [IsAuthenticated, IsTeacher,
        # HasCreditBalance]. Don't "fix" it to match without auditing that
        # override; doing so would newly expose this endpoint to students.
        permission_classes=[IsAuthenticated, IsTeacherOrReadOnly],
        url_path="teacher_feedback",
    )
    def teacher_feedback(self, request, pk=None):
        # get_object() runs get_queryset() (which scopes teachers to their
        # own courses) plus check_object_permissions — using the manager
        # directly here would let any teacher read any submission by pk.
        submission = self.get_object()

        assignment = submission.assignment

        formatted_grade = submission.formatted_grade

        if not formatted_grade:

            grading = submission.feedback

            if grading:

                user_prompt = f"""
                Student Name: {submission.student.get_full_name()}
                Course: {assignment.course}


                Grading Result:

                {grading}

                Return a formatted response
                """

                task_id = None
                processing_task = create_processing_task(
                    requested_by=request.user,
                    task_type=BackgroundTaskType.FORMATTED_GRADE,
                    assignment=assignment,
                    submission=submission,
                    meta={"step": "Queued for formatted grade generation"},
                )
                task = launch_processing_task(
                    formatted_grade_async,
                    processing_task,
                    str(submission.id),
                    user_prompt,
                )
                task_id = task.id

                # formatted_grade = ai_processor.formatted_grade(user_prompt)

                # submission.formatted_grade = formatted_grade
                # submission.save()

                data = {
                    "submission_id": submission.id,
                    "task_id": task_id,
                    "message": "Retrieving teacher feedback",
                }

                serializer = StudentSubmissionFormattedGradeAsyncSerializer(data)

                return Response(serializer.data, status=HTTP_200_OK)

            else:
                return Response("Submission has not be graded yet")

        serializer = StudentSubmissionTeacherFeedbackSerializer(submission)

        return Response(serializer.data)

    @extend_schema(
        tags=["07 Student Submissions"],
        summary="Update the grade for a student submission",
        description="Allows a teacher to manually update the score and feedback for a student submission.",
        request=StudentSubmissionGradeUpdateSerializer,
        responses={
            200: StudentSubmissionSerializer,
            400: OpenApiResponse(description="Invalid input"),
            404: OpenApiResponse(description="Student submission not found"),
        },
    )
    @action(
        detail=True,
        methods=["PATCH"],
        permission_classes=[IsAuthenticated, IsTeacher, HasCreditBalance],
        url_path="update-grade",
        url_name="update-grade",
    )
    def update_grade(self, request, pk=None):
        submission = self.get_object()
        serializer = StudentSubmissionGradeUpdateSerializer(
            submission, data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)

        feedback = submission.feedback

        if not feedback:
            return Response(
                "Submission has not be graded yet", status=HTTP_400_BAD_REQUEST
            )

        # partial=True makes every serializer field optional, so a PATCH
        # without "score" passes validation with empty validated_data —
        # guard explicitly instead of KeyError-500ing.
        if "score" not in serializer.validated_data:
            return Response(
                {"error": "A 'score' value is required."},
                status=HTTP_400_BAD_REQUEST,
            )

        try:
            score = float(serializer.validated_data["score"])
        except (TypeError, ValueError):
            return Response(
                {"error": "Score must be a number."},
                status=HTTP_400_BAD_REQUEST,
            )

        try:
            max_total_points = float(feedback["grading_summary"]["max_total_points"])
        except (KeyError, TypeError, ValueError):
            max_total_points = float(submission.max_points or 0)

        if max_total_points <= 0:
            return Response(
                {
                    "error": (
                        "This submission has no recorded maximum points, so "
                        "a percentage cannot be calculated. Re-grade the "
                        "submission first."
                    )
                },
                status=HTTP_400_BAD_REQUEST,
            )

        # A manual override must stay within the assignment's bounds — an
        # unclamped PATCH could store 500/10 (and a percentage >= 1000
        # crashes at save time on the 5-digit decimal column).
        if score < 0 or score > max_total_points:
            return Response(
                {
                    "error": (
                        f"Score must be between 0 and "
                        f"{max_total_points:g} for this assignment."
                    )
                },
                status=HTTP_400_BAD_REQUEST,
            )

        percentage = round((score / max_total_points) * 100, 2)

        feedback.setdefault("grading_summary", {})
        feedback["grading_summary"]["total_score"] = score
        feedback["grading_summary"]["percentage"] = percentage
        feedback["grading_summary"].setdefault("max_total_points", max_total_points)

        submission.score = float(score)
        submission.score_percentage = percentage
        submission.max_points = int(max_total_points)

        submission.feedback = feedback
        submission.was_regraded = True
        submission.regraded_at = timezone.now()

        # A manual override IS the teacher's resolution of any pending
        # grader-disagreement review — clear the queue flag and record
        # how it was resolved (labeled data for the eval loop).
        if submission.needs_review:
            submission.review_reasons = (submission.review_reasons or []) + [
                {
                    "resolved": "overridden",
                    "by": str(request.user.id),
                    "at": timezone.now().isoformat(),
                }
            ]
        submission.needs_review = False

        # Update the formatted grade since the score/feedback changed

        submission.save(
            update_fields=[
                "score",
                "score_percentage",
                "max_points",
                "feedback",
                "was_regraded",
                "regraded_at",
                "needs_review",
                "review_reasons",
            ]
        )

        if submission.is_published:
            notify_student_of_graded_submission(submission, is_update=True)

        assignment = submission.assignment
        user_prompt = f"""
        Student Name: {submission.student.get_full_name()}
        Course: {assignment.course}


        Grading Result:

        {submission.feedback}

        Return a formatted response
        """

        # Tracked dispatch (BackgroundProcessingTask + launch), matching
        # every other formatted-grade call site — a bare .delay() here made
        # the regrade's formatting step invisible and uncancellable.
        formatted_processing_task = create_processing_task(
            requested_by=request.user,
            task_type=BackgroundTaskType.FORMATTED_GRADE,
            assignment=assignment,
            submission=submission,
            meta={"step": "Queued for formatted grade generation"},
        )
        launch_processing_task(
            formatted_grade_async,
            formatted_processing_task,
            str(submission.id),
            user_prompt,
        )

        response_serializer = StudentSubmissionDetailSerializer(submission)
        return Response(response_serializer.data, status=HTTP_200_OK)

    # @action(
    #     detail=False, methods=["POST"], url_path=r"batch_upload/(?P<assignment_id>[-\w]+)",
    #     permission_classes=[IsAuthenticated, IsTeacher],
    # )
    # def teacher_batch_upload(self, request, assignment_id=None, *args, **kwargs):
    #     """
    #      Teachers can upload multiple submissions for students at once.
    #      Files: multipart/form-data "files"
    #      Optional: student_info_list: JSON list of IDs or names
    #      """
    #     files = request.FILES.getlist("files")
    #     if not files:
    #         raise ParseError("No files uploaded. Please try again.")

    @extend_schema(
        tags=["07 Student Submissions"],
        operation_id="batch_upload_student_submissions",
        summary="Batch upload student submissions for an assignment",
        description="""
    Allows a **teacher** to upload multiple student submissions for a specific assignment in a single request.

    Each uploaded file represents a student's submission.
    The system will process each submission **asynchronously** using Celery workers.


    ### Background Processing

    Each uploaded file produces a **separate Celery task**.
    The returned `task_ids` can be used to track processing progress.

    ### Example Use Case

    Teacher uploads **30 scanned assignment papers**.

    The system:

    - starts 30 background tasks
    - extracts answers from each submission
    - creates submissions
    - returns 30 task IDs immediately.

    This avoids request timeouts and allows scalable parallel processing.
    """,
        request={
            "multipart/form-data": {
                "type": "object",
                "properties": {
                    "answers": {
                        "type": "array",
                        "items": {"type": "string", "format": "binary"},
                        "description": "List of assignment submission files to upload.",
                    }
                },
                "required": ["answers"],
            }
        },
        responses={
            202: OpenApiResponse(
                description="Batch upload accepted and background tasks started.",
                response=BatchUploadResponseSerializer,
            ),
            400: OpenApiResponse(
                description="Bad request. No files were uploaded or invalid request format.",
                examples=[
                    OpenApiExample(
                        "No files uploaded",
                        value={"detail": "No files uploaded. Please try again."},
                    )
                ],
            ),
            403: OpenApiResponse(
                description="Permission denied. User is not a teacher.",
            ),
        },
    )
    @action(
        detail=False,
        methods=["POST"],
        permission_classes=[IsAuthenticated, IsTeacher, HasCreditBalance],
        url_path=r"(?P<assignment_id>[-\w]+)/batch-upload",
    )
    def batch_upload(self, request, assignment_id=None):
        assignment = get_object_or_404(Assignment, id=assignment_id)
        files = request.FILES.getlist("answers")

        if not files:
            raise ParseError("No files uploaded. Please try again.")

        # Validate every file up front, before the session/Celery tasks for
        # any of them are created - one oversized file in a batch shouldn't
        # leave a half-queued session behind.
        for uploaded_file in files:
            validate_upload_size(uploaded_file)

        session = BatchUploadSession.objects.create(
            teacher=request.user,
            assignment=assignment,
            task_type=BatchUploadType.SUBMISSION,
            total_files=len(files),
        )

        tasks_data = []
        task_ids = []

        for uploaded_file in files:
            prompt = """
            Analyze the image of an educational assignment and return a JSON

            IMPORTANT: Return only valid JSON matching the required structure.
            Do not include any explanatory text before or after the JSON
            """

            # Read raw file bytes cheaply here; heavy rasterization/compression
            # happens inside the Celery task, not on the request thread.
            file_payload = AssignmentProcessingService.build_async_upload_payload(
                uploaded_file
            )

            # Trigger individual async tasks for each paper
            # This allows parallel processing in Celery
            processing_task = create_processing_task(
                requested_by=request.user,
                task_type=BackgroundTaskType.BATCH_ANSWER_UPLOAD,
                batch_session=session,
                assignment=assignment,
                file_name=uploaded_file.name,
                meta={"step": "Queued for batch answer extraction"},
            )
            task = launch_processing_task(
                upload_answers_engine_async,
                processing_task,
                str(assignment.id),
                file_payload,
                prompt,
                str(request.user.id),
                session_id=str(session.id),
                file_name=uploaded_file.name,
            )
            tasks_data.append({"file_name": uploaded_file.name, "task_id": task.id})
            task_ids.append(task.id)

        data = {
            "session_id": session.id,
            "message": f"Batch processing started for {len(files)} files",
            "tasks": tasks_data,
        }

        serializer = BatchUploadResponseSerializer(data)

        return Response(
            serializer.data,
            status=HTTP_202_ACCEPTED,
        )

    @extend_schema(
        tags=["07 Student Submissions"],
        summary="Publish a student's grade",
        description="Release the grade and feedback to the student. Only works if the submission has been graded.",
        responses={
            200: StudentSubmissionDetailSerializer,
            400: OpenApiResponse(description="Submission is not graded yet"),
            404: OpenApiResponse(description="Submission not found"),
        },
    )
    @action(
        detail=True,
        methods=["POST"],
        permission_classes=[IsAuthenticated, IsTeacher],
        url_path="publish",
    )
    def publish_grade(self, request, pk=None):
        submission = self.get_object()

        # A submission is publishable only when grading actually finished:
        # BOTH a grading timestamp and a score. Requiring only one let a
        # half-graded row (graded_at set by a run that failed before
        # persisting a score, or vice versa) be published, emailing the
        # student about a grade that doesn't exist.
        if not submission.graded_at or submission.score is None:
            return Response(
                {"error": "Cannot publish an ungraded submission."},
                status=HTTP_400_BAD_REQUEST,
            )

        # Conditional UPDATE as an atomic claim: two concurrent publish
        # requests both saw is_published=False above, but only one matches
        # this WHERE clause — so the student gets exactly one notification
        # instead of one per click.
        newly_published = StudentSubmission.objects.filter(
            pk=submission.pk, is_published=False
        ).update(is_published=True)
        submission.is_published = True

        if newly_published:
            notify_student_of_graded_submission(submission)
            # .update() bypasses post_save, so the cache-invalidation
            # signal (students.signals.clear_student_submission_cache)
            # never fires — without this a student polling their
            # submission keeps seeing it unpublished (and their grade
            # withheld) for up to CACHE_TTL after the teacher published it.
            delete_cache_patterns(
                "*superadmin*",
                "*schooladmin*",
                "*teacheradmin*",
                "*studentadmin*",
                "courses:*",
                "assignments:*",
                "studentsubmissions:*",
            )

        serializer = StudentSubmissionDetailSerializer(
            submission, context=self.get_serializer_context()
        )
        return Response(serializer.data, status=HTTP_200_OK)

    @extend_schema(
        tags=["07 Student Submissions"],
        summary="Resolve a grader-disagreement review as 'AI grade confirmed'",
        request=None,
        responses={HTTP_200_OK: StudentSubmissionDetailSerializer},
    )
    @action(
        detail=True,
        methods=["POST"],
        url_path="mark-reviewed",
        url_name="mark-reviewed",
    )
    def mark_reviewed(self, request, pk=None):
        """
        The teacher looked at both graders' rationales and confirms the
        stored grade stands. Clears the review-queue flag and records the
        resolution — the "AI was right" label the future eval loop
        consumes. (The other resolution path is update-grade, which
        records "overridden".)
        """
        submission = self.get_object()

        # Conditional UPDATE claim, same pattern as publish: of two racing
        # requests only one matches needs_review=True, so the resolution
        # entry is appended exactly once. Already-resolved submissions are
        # an idempotent no-op, not an error.
        resolved = StudentSubmission.objects.filter(
            pk=submission.pk, needs_review=True
        ).update(
            needs_review=False,
            review_reasons=(submission.review_reasons or [])
            + [
                {
                    "resolved": "confirmed",
                    "by": str(request.user.id),
                    "at": timezone.now().isoformat(),
                }
            ],
        )
        if resolved:
            submission.refresh_from_db(fields=["needs_review", "review_reasons"])
            # .update() bypasses post_save, so the cache-invalidation
            # signal (students.signals.clear_student_submission_cache)
            # never fires — without this the cached detail payload keeps
            # reporting needs_review=true for up to CACHE_TTL.
            delete_cache_patterns(
                "*superadmin*",
                "*schooladmin*",
                "*teacheradmin*",
                "*studentadmin*",
                "courses:*",
                "assignments:*",
                "studentsubmissions:*",
            )

        serializer = StudentSubmissionDetailSerializer(
            submission, context=self.get_serializer_context()
        )
        return Response(serializer.data, status=HTTP_200_OK)

    # @extend_schema(
    #     tags=["07 Student Submissions"],
    #     summary="Retrieve batch upload session results",
    #     description="""
    #     Retrieve the processing status and results of a batch upload session.
    #
    #     This endpoint returns the progress of the background tasks, indicating how many
    #     files have been processed and the overall completion status. It provides lists of
    #     successfully processed submissions and those that failed.
    #     """,
    #     responses={
    #         200: OpenApiResponse(
    #             description="Session results retrieved successfully.",
    #             response=OpenApiTypes.OBJECT,
    #             examples=[
    #                 OpenApiExample(
    #                     "In Progress",
    #                     value={
    #                         "progress": "2 / 3",
    #                         "is_complete": False,
    #                         "success_count": 2,
    #                         "failure_count": 0,
    #                         "success_list": [
    #                             {
    #                                 "status": "SUCCESS",
    #                                 "file_name": "student_a.pdf",
    #                                 "submission_id": "b2c3d4e5",
    #                             },
    #                         ],
    #                         "failure_list": [],
    #                     },
    #                 ),
    #                 OpenApiExample(
    #                     "Completed with failures",
    #                     value={
    #                         "progress": "3 / 3",
    #                         "is_complete": True,
    #                         "success_count": 2,
    #                         "failure_count": 1,
    #                         "success_list": [
    #                             {"status": "SUCCESS", "file_name": "student_a.pdf"},
    #                         ],
    #                         "failure_list": [
    #                             {
    #                                 "status": "FAILED",
    #                                 "file_name": "unknown_file.pdf",
    #                                 "error": "Could not identify or associate a student with this paper",
    #                             }
    #                         ],
    #                     },
    #                 ),
    #             ],
    #         ),
    #         404: OpenApiResponse(
    #             description="Session not found.",
    #         ),
    #     },
    # )
    # @action(detail=True, methods=["GET"], url_path="session-results")
    # def session_results(self, request, pk=None):
    #     session = get_object_or_404(BatchUploadSession, id=pk)
    #
    #     # Separate into two clean lists for the UI
    #     success = [r for r in session.results if r["status"] == "SUCCESS"]
    #     failures = [r for r in session.results if r["status"] == "FAILED"]
    #
    #     completed = len(session.results)
    #     total = session.total_files
    #
    #     percentage = (completed / total) * 100 if total > 0 else 0
    #
    #     return Response(
    #         {
    #             "progress": f"{completed} / {total}",
    #             "percent": round(percentage),
    #             "is_complete": completed == total,
    #             "success_count": len(success),
    #             "failure_count": len(failures),
    #             "success_list": success,
    #             "failure_list": failures,
    #         }
    #     )


class StudentViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = StudentListSerializer
    filter_backends = [
        DjangoFilterBackend,
        filters.SearchFilter,
        filters.OrderingFilter,
    ]

    filterset_fields = {
        "enrollments__course": ["exact"],
        "enrollments__course__session": ["exact"],
    }

    search_fields = ["first_name", "last_name", "middle_name", "email"]

    def get_queryset(self):
        user = self.request.user

        return CustomUser.objects.filter(enrollments__course__teacher=user).distinct()
