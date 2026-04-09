# from django.shortcuts import render
import json
import uuid

from django.conf import settings
from django.core.cache import cache
from django.core.files.uploadedfile import UploadedFile
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
from rest_framework.exceptions import NotAcceptable, NotFound, ParseError
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.status import (
    HTTP_200_OK,
    HTTP_201_CREATED,
    HTTP_202_ACCEPTED,
    HTTP_400_BAD_REQUEST,
    HTTP_500_INTERNAL_SERVER_ERROR,
)

from ai_processor.services import ai_processor
from assignments.models import Assignment
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
from classrooms.permissions import IsStudent, IsTeacher, IsTeacherOrReadOnly
from users.mixins import UserCacheMixin
from users.models import CustomUser, UserTypes
from users.permissions import HasCreditBalance

from .models import BatchUploadSession, BatchUploadType, StudentSubmission
from .serializers import (
    StudentListSerializer,
    StudentSubmissionDetailSerializer,
    StudentSubmissionFormattedGradeAsyncSerializer,
    StudentSubmissionGradeAsyncSerializer,
    StudentSubmissionGradeUpdateSerializer,
    StudentSubmissionListSerializer,
    StudentSubmissionSerializer,
    StudentSubmissionTeacherFeedbackSerializer,
    StudentSubmissionUpdateSerializer,
    StudentSubmissionUploadAsyncSerializer,
)
from .services import grade_engine, student_submission_to_html, upload_answers_engine

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
    pagination_class = PageNumberPagination
    http_method_names = ["get", "head", "post", "delete", "patch", "options"]

    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]

    filterset_fields = [
        "assignment",
    ]
    search_fields = ["student__first_name", "student__last_name"]
    ordering_fields = ["student__first_name", "student__last_name"]
    ordering = ["student__first_name"]

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
            submission.raw_input = AssignmentProcessingService.html_to_prosemirror_json(
                answer_html
            )
            submission.save(update_fields=["raw_input"])

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
            return StudentSubmission.objects.filter(student=user)
        elif user.user_type == UserTypes.TEACHER:
            return StudentSubmission.objects.filter(assignment__course__teacher=user)
        else:
            return StudentSubmission.objects.none()

    def get_serializer_class(self):
        if self.action == "list":
            return StudentSubmissionListSerializer
        elif self.action == "retrieve":
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
            "partial_update",
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
            return Response({"error": str(e)}, status=HTTP_500_INTERNAL_SERVER_ERROR)

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

        prompt = """
        Analyze the image of an educational assignment and return a JSON

        IMPORTANT: Return only valid JSON matching the required structure.
        Do not include any explanatory text before or after the JSON
        """

        content = AssignmentProcessingService.prepare_ai_content(uploaded_file, prompt)

        task_id = None

        task = upload_answers_engine_async.delay(
            str(assignment.id), content, str(request.user.id)
        )
        task_id = task.id

        data = {"task_id": task_id, "message": "Answer Extraction Started"}

        serializer = StudentSubmissionUploadAsyncSerializer(data)

        return Response(serializer.data, status=HTTP_200_OK)

    def partial_update(self, request, *args, **kwargs):
        raw_input = request.data.get("raw_input")

        submission = self.get_object()
        assignment = submission.assignment

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
                    AssignmentProcessingService.html_to_prosemirror_json(answer_html)
                )
                submission.save()

                serializer = StudentSubmissionListSerializer(submission)

                return Response(serializer.data, status=HTTP_201_CREATED)
        except Exception as e:
            return Response({"error": str(e)}, status=HTTP_500_INTERNAL_SERVER_ERROR)

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
        # Validate submission id
        try:
            submission = StudentSubmission.objects.get(pk=pk)
        except StudentSubmission.DoesNotExist:
            raise NotFound(
                detail="No Student Submission with this ID is found"
            ) from StudentSubmission.DoesNotExist

        try:
            submission = grade_engine(request.user, submission)
            serializer = StudentSubmissionDetailSerializer(submission)

            return Response(serializer.data, status=HTTP_200_OK)

        except Exception as e:
            return Response({"error": str(e)}, status=HTTP_500_INTERNAL_SERVER_ERROR)

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

        task = grade_engine_async.delay(str(request.user.id), str(submission.id))
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
            # kwargs=json.dumps({
            #     "user_id": str(request.user.id),
            #     "submission_id": str(submission.id)
            # }),
        )

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
        permission_classes=[IsAuthenticated, IsTeacherOrReadOnly],
        url_path="teacher_feedback",
    )
    def teacher_feedback(self, request, pk=None):
        try:
            submission = StudentSubmission.objects.get(pk=pk)
        except StudentSubmission.DoesNotExist:
            raise NotFound(
                detail="No Student Submission with this ID is found"
            ) from StudentSubmission.DoesNotExist

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
                task = formatted_grade_async.delay(str(submission.id), user_prompt)
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

        score = serializer.validated_data["score"]
        total_score = feedback["grading_summary"]["total_score"]
        percentage = (score / total_score) * 100

        feedback["grading_summary"]["total_score"] = score
        feedback["grading_summary"]["percentage"] = percentage

        submission.score = score
        submission.score_percentage = percentage
        submission.max_points = total_score

        submission.feedback = feedback
        submission.was_regraded = True
        submission.regraded_at = timezone.now()
        # submission.save(update_fields=["score", "feedback"])

        # Update the formatted grade since the score/feedback changed
        assignment = submission.assignment
        user_prompt = f"""
        Student Name: {submission.student.get_full_name()}
        Course: {assignment.course}


        Grading Result:

        {submission.feedback}

        Return a formatted response
        """
        submission.formatted_grade = ai_processor.formatted_grade(
            request.user, user_prompt, assignment_model=assignment
        )
        submission.save(
            update_fields=[
                "score",
                "score_percentage",
                "max_points",
                "feedback",
                "formatted_grade",
                "was_regraded",
                "regraded_at",
            ]
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

            # Prepare content for each file
            content = AssignmentProcessingService.prepare_ai_content(
                uploaded_file, prompt
            )

            # Trigger individual async tasks for each paper
            # This allows parallel processing in Celery
            task = upload_answers_engine_async.delay(
                str(assignment.id),
                content,
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

        if not submission.graded_at and submission.score is None:
            return Response(
                {"error": "Cannot publish an ungraded submission."},
                status=HTTP_400_BAD_REQUEST,
            )

        submission.is_published = True
        submission.save(update_fields=["is_published"])

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
