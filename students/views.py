# from django.shortcuts import render
from django.core.files.uploadedfile import UploadedFile
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
    HTTP_500_INTERNAL_SERVER_ERROR,
)

from ai_processor.services import ai_processor, pdf_service
from ai_processor.tools import encode_image
from assignments.models import Assignment
from classrooms.permissions import IsStudent, IsTeacher
from students.models import StudentSubmission
from students.serializers import StudentSubmissionSerializer
from users.models import UserTypes

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
            200: StudentSubmissionSerializer(many=True),
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
            200: StudentSubmissionSerializer,
            404: OpenApiResponse(description="Student submission not found"),
            500: OpenApiResponse(description="Internal Server Error"),
        },
    ),
    partial_update=extend_schema(
        tags=["07 Student Submissions"],
        summary="Partially update a student submission",
        description="Update one or more fields of an existing student submission.",
        request=StudentSubmissionSerializer(partial=True),
        responses={
            200: StudentSubmissionSerializer,
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

    @method_decorator(cache_page(60 * 5, key_prefix="studentsubmissions:list"))
    @method_decorator(vary_on_headers("Authorization"))
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @method_decorator(cache_page(60 * 5, key_prefix="studentsubmissions:detail"))
    @method_decorator(vary_on_headers("Authorization"))
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)

    def get_queryset(self):
        user = self.request.user

        if user.user_type == UserTypes.STUDENT:
            return StudentSubmission.objects.filter(student=user)
        elif user.user_type == UserTypes.TEACHER:
            return StudentSubmission.objects.filter(assignment__course__teacher=user)
        else:
            return StudentSubmission.objects.none()

    def get_permissions(self):
        if self.action in ["create", "upload_answers"]:
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
                response=StudentSubmissionSerializer,
                description="Answer processed successfully",
                examples=[
                    OpenApiExample(
                        name="Answer Example",
                        value={
                            "assignment": 1,
                            "answers": [
                                {
                                    "question_number": 1,
                                    "question_text": "What color is the sky?",
                                    "answer_text": "Purple",
                                    "notes": "OCR error corrected: 'Purle' to 'Purple'.",
                                },
                                {
                                    "question_number": 2,
                                    "question_text": "What color is the ground?",
                                    "answer_text": "Orange",
                                    "notes": "",
                                },
                                {
                                    "question_number": 3,
                                    "question_text": "What color is the sun?",
                                    "answer_text": "Black",
                                    "notes": "",
                                },
                            ],
                            "ai_confidence_score": 95,
                            "general_feedback": "All questions answered. Minor OCR formatting issue "
                            "between Q5 and Q6 resolved by context. Some answers are "
                            "unconventional or incorrect based on common knowledge, "
                            "but preserved as student's response.",
                        },
                        response_only=True,
                    )
                ],
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
        if not Assignment.objects.filter(id=assignment_id).exists():
            raise NotFound(detail="No Assignment with this ID is found")

        files = request.FILES.getlist("answer")
        if not files:
            raise ParseError("No files uploaded. Please try again.")

        if len(files) > 1:
            raise NotAcceptable(detail="Only one file can be uploaded at a time")

        image_formats = [
            "image/jpeg",
            "image/png",
            "image/gif",
            "image/webp",
        ]

        pdf_formats = "application/pdf"

        extracted_text = ""

        for uploaded_file in files:
            if not isinstance(uploaded_file, UploadedFile):
                raise ParseError(
                    "Invalid file upload. Only images (JPEG, PNG, GIF, WebP) and PDFs are allowed."
                )

            text = """
            Analyze the image of an educational assignment and return a JSON

            IMPORTANT: Return only valid JSON matching the required structure.
            Do not include any explanatory text before or after the JSON
            """

            if uploaded_file.content_type in image_formats:
                try:
                    base64_encoded_file = encode_image(uploaded_file)

                    content = [
                        {"type": "text", "text": text},
                        {
                            "type": "image_url",
                            "image_url": f"data:{uploaded_file.content_type};base64,{base64_encoded_file}",
                        },
                    ]

                    student_submission = ai_processor.extract_answer_with_retry(
                        content, max_retries=3
                    )

                    if student_submission is not None:
                        student_submission["assignment"] = assignment_id

                    return Response(student_submission, status=HTTP_201_CREATED)

                except Exception as e:
                    return Response(
                        {"error": str(e)}, status=HTTP_500_INTERNAL_SERVER_ERROR
                    )
            elif uploaded_file.content_type == pdf_formats:
                try:
                    pdf_service.set_uploaded_file(uploaded_file)
                    images_base64_encoded = pdf_service.extract()

                    content = [{"type": "text", "text": text}]

                    for image in images_base64_encoded:
                        content.append(
                            {
                                "type": "image_url",
                                "image_url": f"data:image/PNG;base64,{image}",
                            }
                        )

                    student_submission = ai_processor.extract_answer_with_retry(
                        content, max_retries=3
                    )

                    if student_submission is not None:
                        student_submission["assignment"] = assignment_id

                    return Response(student_submission, status=HTTP_201_CREATED)

                except Exception as e:
                    return Response(
                        {"error": str(e)}, status=HTTP_500_INTERNAL_SERVER_ERROR
                    )
            else:
                raise ParseError(
                    "Invalid file upload. Only images (JPEG, PNG, GIF, WebP) and PDFs are allowed."
                )

        try:
            answer = ai_processor.extract_answer_with_retry(
                extracted_text, max_retries=3
            )
        except Exception as e:

            return Response(
                {"error": "We encountered an error: {}".format(str(e))},
                status=HTTP_500_INTERNAL_SERVER_ERROR,
            )

        if answer is not None:
            answer["assignment"] = int(assignment_id)

        return Response(answer, status=HTTP_201_CREATED)

    @extend_schema(tags=["07 Student Submissions"])
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

        assignment = submission.assignment

        # if not hasattr(assignment, "rubric"):
        #     raise NotFound(detail="No Rubric for this assignment")

        # rubric = assignment.rubric

        # if not rubric.has_criteria():
        #     raise NotFound(detail="No Rubric criteria found for this assignment")

        try:
            # rubric_json = rubric.get_rubric_criteria_json()
            answer_json = submission.get_answer()
            submission.ai_graded_at = timezone.now()

            grading = ai_processor.extract_grade_with_retry(
                assignment.questions, answer_json
            )

            grading_score = grading["grading_summary"]["total_score"]
            grading_confidence = grading["grader_meta_analysis"]["grading_confidence"]

            submission.score = grading_score
            submission.feedback = grading
            submission.grading_confidence = grading_confidence

            submission.ai_score = grading_score
            submission.ai_grading_completed_at = timezone.now()

            submission.save()

        except Exception as e:
            return Response({"error": str(e)}, status=HTTP_500_INTERNAL_SERVER_ERROR)

        return Response(grading, status=HTTP_200_OK)
