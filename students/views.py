# from django.shortcuts import render
from django.core.exceptions import BadRequest
from django.core.files.uploadedfile import UploadedFile
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
)
from PIL.Image import Image
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotAcceptable, NotFound
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.status import (
    HTTP_200_OK,
    HTTP_201_CREATED,
    HTTP_415_UNSUPPORTED_MEDIA_TYPE,
    HTTP_500_INTERNAL_SERVER_ERROR,
)

from ai_processor.services import ai_processor, ocr_service, pdf_service
from assignments.models import Assignment
from classrooms.permissions import IsTeacher
from students.models import StudentSubmission
from students.serializers import StudentSubmissionSerializer

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
    )
    def upload_answers(self, request, assignment_id=None, *args, **kwargs):
        if not Assignment.objects.filter(id=assignment_id).exists():
            raise NotFound(detail="No Assignment with this ID is found")

        files = request.FILES.getlist("answer")
        if not files:
            raise BadRequest()

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
                raise BadRequest(
                    "Invalid file upload. Only images (JPEG, PNG, GIF, WebP) and PDFs are allowed."
                )

            if uploaded_file.content_type in image_formats:
                try:
                    image = Image.open(uploaded_file)
                    extracted_text = ocr_service.extract_with_pytessaract(image)
                except Exception as e:
                    return Response(
                        {"error": str(e)}, status=HTTP_500_INTERNAL_SERVER_ERROR
                    )
            elif uploaded_file.content_type == pdf_formats:
                try:
                    pdf_service.set_uploaded_file(uploaded_file)
                    extracted_data = pdf_service.extract()
                    extracted_text = extracted_data["questions"]

                except Exception as e:
                    return Response(
                        {"error": str(e)}, status=HTTP_500_INTERNAL_SERVER_ERROR
                    )
            else:
                return Response(
                    {
                        "error": "Invalid file upload. Only images (JPEG, PNG, GIF, WebP) and PDFs are allowed."
                    },
                    status=HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                )

        try:
            answer = ai_processor.extract_answer_with_retry(
                extracted_text, max_retries=3
            )
        except Exception as e:
            return Response(
                {"error": "We encountered an error: {}".format(str(e))},
            )

        if answer is not None:
            answer["assignment"] = int(assignment_id)

        return Response(answer, status=HTTP_201_CREATED)

    @extend_schema(tags=["07 Student Submissions"])
    @action(
        detail=True, methods=["GET"], permission_classes=[IsAuthenticated, IsTeacher]
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

        if not hasattr(assignment, "rubric"):
            raise NotFound(detail="No Rubric for this assignment")

        rubric = assignment.rubric

        if not rubric.has_criteria():
            raise NotFound(detail="No Rubric criteria found for this assignment")

        try:
            rubric_json = rubric.get_rubric_criteria_json()
            answer_json = submission.get_answer()

            grading = ai_processor.extract_grade_with_retry(rubric_json, answer_json)

        except Exception as e:
            return Response({"error": str(e)}, status=HTTP_500_INTERNAL_SERVER_ERROR)

        return Response(grading, status=HTTP_200_OK)

    @extend_schema(
        tags=["07 Student Submissions"],
        summary="Save Student Grade and AI generated feedback",
        description="""
        Save a student's grade and feedback for a submission.

        This endpoint allows instructors to save the final grade and feedback for a student's submission.
        The grade and feedback are typically generated by the AI grading system and then reviewed/modified
        by the instructor before being saved.

        ## Request Body
        - `total_score` (number, required): The final score for the submission
        - Additional feedback fields can be included and will be stored in the feedback JSON field

        ## Response
        - 200: Success - Grade saved successfully
        - 404: Not Found - If no submission exists with the given ID
        """,
        request={
            "application/json": {
                "type": "object",
                "properties": {
                    "total_score": {
                        "type": "number",
                        "description": "Total points scored by the student",
                    },
                    "max_total_points": {
                        "type": "number",
                        "description": "Maximum possible points for the assignment",
                    },
                    "evaluation_details": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "question_id": {
                                    "type": "number",
                                    "description": "ID of the question",
                                },
                                "question_text": {
                                    "type": "string",
                                    "description": "Text of the question",
                                },
                                "student_answer": {
                                    "type": "string",
                                    "description": "Student's answer to the question",
                                },
                                "model_answer": {
                                    "type": "string",
                                    "description": "Model answer to the question",
                                },
                                "score_received": {
                                    "type": "number",
                                    "description": "Points awarded to the student for this question",
                                },
                                "level_achieved": {
                                    "type": "string",
                                    "description": "Performance level achieved by the student",
                                },
                                "feedback": {
                                    "type": "string",
                                    "description": "Feedback for the student",
                                },
                            },
                        },
                    },
                    "summary_and_overall_feedback": {
                        "type": "object",
                        "properties": {
                            "overall_score_breakdown": {
                                "type": "string",
                                "description": "Summary of the total score",
                            },
                            "strengths": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "List of strengths observed across the submission",
                            },
                            "areas_for_improvement": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "List of areas where the student can improve",
                            },
                        },
                    },
                    "grader_evaluation": {
                        "type": "object",
                        "properties": {
                            "grading_confidence_score": {
                                "type": "number",
                                "description": "Self-evaluated confidence in the accuracy of the grading",
                            },
                            "grading_issues": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "List of issues encountered during grading",
                            },
                            "recommendations_for_teacher": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Suggestions for the teacher",
                            },
                        },
                        "required": [
                            "grading_confidence_score",
                            "grading_issues",
                            "recommendations_for_teacher",
                        ],
                    },
                },
                "required": [
                    "total_score",
                    "max_total_points",
                    "evaluation_details",
                    "summary_and_overall_feedback",
                    "grader_evaluation",
                ],
                "example": STUDENT_RESPONSE_EXAMPLE,
            }
        },
        responses={
            200: OpenApiResponse(
                description="Grade saved successfully",
                examples=[
                    OpenApiExample(
                        name="successful", value={"status": "Grade saved successfully"}
                    )
                ],
            ),
            404: OpenApiResponse(
                description="Student submission not found",
                examples=[
                    OpenApiExample(
                        name="not found",
                        value={"error": "Student submission not found"},
                    )
                ],
            ),
            500: OpenApiResponse(
                description="Internal server error",
                examples=[
                    OpenApiExample(
                        name="internal server error",
                        value={"error": "Internal server error"},
                    )
                ],
            ),
        },
    )
    @action(
        detail=True,
        methods=["POST"],
    )
    def save_grade(self, request, pk=None):
        try:
            submission = StudentSubmission.objects.get(pk=pk)
        except StudentSubmission.DoesNotExist:
            raise NotFound(
                detail="No Student Submission with this ID is found"
            ) from StudentSubmission.DoesNotExist

        data = request.data

        submission.score = data["total_score"]
        submission.feedback = dict(data)
        submission.save(update_fields=["score", "feedback"])

        # serializer = StudentSubmissionSerializer(submission)

        return Response(
            {"status": "Grade Saved successfully"}, status=status.HTTP_200_OK
        )
