# from django.shortcuts import render
from django.core.files.uploadedfile import UploadedFile
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

# from PIL.Image import Image
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotAcceptable, NotFound, ParseError
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.status import (
    HTTP_200_OK,
    HTTP_201_CREATED,
    HTTP_400_BAD_REQUEST,
    HTTP_500_INTERNAL_SERVER_ERROR,
)

from ai_processor.services import ai_processor
from assignments.models import Assignment
from assignments.services import AssignmentProcessingService
from assignments.tasks import (
    formatted_grade_async,
    grade_engine_async,
    upload_answers_engine_async,
)
from classrooms.permissions import IsStudent, IsTeacher, IsTeacherOrReadOnly
from users.models import UserTypes

from .models import StudentSubmission
from .serializers import (
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

# Create your views here.

STUDENT_RESPONSE_EXAMPLE = {
    "total_score": 40,
    "max_total_points": 100,
    "evaluation_details": [
        {
            "question_id": 1,
            "question_text": "Define “digital divide” and explain why it matters in "
            "discussions about social media use.",
            "student_answer": "The digital divide is when some people are not on social media.",
            "model_answer": "",
            "score_received": 0,
            "level_achieved": "Poor",
            "feedback": "Your definition of the digital divide is inaccurate and overly simplistic, "
            "as it focuses only on not using social media rather than the broader gap in "
            "access to technology and internet resources. You did not provide any explanation "
            "of its importance in social media discussions, which is required for even a "
            "partial score. To improve, research the standard definition (e.g., disparities in "
            "digital access affecting information equity) and connect it to how it excludes "
            "groups from social media benefits or risks, aiming for the 'Good' level by including "
            "limited reasoning.",
            "summary_and_overall_feedback": {
                "overall_score_breakdown": "The student scored 15/75, or 20%.",
                "strengths": [
                    "Basic awareness of social media concepts, such as recognizing positives like communication and "
                    "negatives like 'bad' aspects.",
                    "Attempt to define key terms, even if inaccurately, shows some engagement with the topic.",
                ],
                "areas_for_improvement": [
                    "Lack of depth and specificity across all responses; answers are brief and fail to meet minimum "
                    "requirements for analysis or examples.",
                    "Inaccurate or incomplete addressing of core concepts like digital divide and misinformation's "
                    "democratic impacts.",
                    "Need for better structure, evidence, and connection to real-world contexts, especially in "
                    "essay and paper questions.",
                ],
            },
            "grader_evaluation": {
                "grading_confidence_score": 0.95,
                "grading_issues": [
                    "Student answers are consistently brief and underdeveloped, making full evaluation challenging "
                    "but aligning clearly with lower rubric levels.",
                    "No major ambiguities in the rubric, but student responses for open-ended questions lack the "
                    "expected length and detail.",
                ],
                "recommendations_for_teacher": [
                    "Encourage students to provide more detailed responses in instructions, perhaps with word count "
                    "minimums for essays and papers. ",
                    "Consider adding sample answers or outlines to the assignment to guide depth in "
                    "analytical questions. ",
                    "Review submissions like this one manually to confirm grading, as brevity may indicate "
                    "incomplete work.",
                ],
            },
        },
    ],
}


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
        examples=[],
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
class StudentSubmissionViewSet(viewsets.ModelViewSet):
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

    @method_decorator(cache_page(60 * 3, key_prefix="studentsubmissions:list"))
    @method_decorator(vary_on_headers("Authorization"))
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @method_decorator(cache_page(60 * 3, key_prefix="studentsubmissions:detail"))
    @method_decorator(vary_on_headers("Authorization"))
    def retrieve(self, request, *args, **kwargs):
        submission = self.get_object()

        answer_html = student_submission_to_html(submission)
        submission.raw_input = AssignmentProcessingService.html_to_prosemirror_json(
            answer_html
        )
        submission.save()

        serializer = StudentSubmissionDetailSerializer(
            submission, context=self.get_serializer_context()
        )
        return Response(serializer.data)

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
        if self.action in ["create", "upload_answers", "update"]:
            permission_classes = [IsAuthenticated, IsStudent]
        else:
            permission_classes = [IsAuthenticated]

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
        permission_classes=[IsAuthenticated],
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
        permission_classes=[IsAuthenticated],
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
                request.user, content, assignment_context, assignment_model=assignment, max_retries=3
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
        responses={
            HTTP_200_OK: StudentSubmissionDetailSerializer,
        },
    )
    @action(
        detail=True,
        methods=["GET"],
        permission_classes=[IsAuthenticated, IsTeacher],
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
            submission = grade_engine(submission)
            serializer = StudentSubmissionDetailSerializer(submission)

            return Response(serializer.data, status=HTTP_200_OK)

        except Exception as e:
            return Response({"error": str(e)}, status=HTTP_500_INTERNAL_SERVER_ERROR)

    @extend_schema(
        tags=["07 Student Submissions"],
        responses={
            HTTP_200_OK: StudentSubmissionDetailSerializer,
        },
    )
    @action(
        detail=True,
        methods=["GET"],
        permission_classes=[IsAuthenticated],
        url_path="grade-async",
    )
    def grade_async(self, request, pk=None):
        try:
            submission = StudentSubmission.objects.get(pk=pk)
        except StudentSubmission.DoesNotExist:
            raise NotFound(
                detail="No Student Submission with this ID is found"
            ) from StudentSubmission.DoesNotExist

        task_id = None

        task = grade_engine_async.delay(str(submission.id))
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
        responses={
            HTTP_200_OK: StudentSubmissionTeacherFeedbackSerializer,
        },
    )
    @method_decorator(
        cache_page(60 * 3, key_prefix="studentsubmissions:formatted_grade")
    )
    @method_decorator(vary_on_headers("Authorization"))
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
        permission_classes=[IsAuthenticated, IsTeacher],
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
        submission.save(update_fields=["score", "feedback", "formatted_grade"])

        response_serializer = StudentSubmissionDetailSerializer(submission)
        return Response(response_serializer.data, status=HTTP_200_OK)
