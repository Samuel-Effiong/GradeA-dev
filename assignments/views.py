from django.core.files.uploadedfile import UploadedFile
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
)
from PIL import Image
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from ai_processor.services import ai_processor, ocr_service, pdf_service

from .models import Assignment
from .serializers import AssignmentSerializer

# from ai_processor.validators import AssignmentStructure

# from assignments.services import PDFService


# from assignments.services import PDFService

RESPONSE_FORMAT_EXAMPLE = {
    "assignment_name": "World History - Industrial Revolution Quiz",
    "subject_name": "History",
    "instructions": "Answer all questions to the best of your ability. Write your answers in the spaces provided. "
    "For multiple choice questions, select the best answer. Use complete sentences for essay questions.",
    "total_points": 50,
    "question_count": 5,
    "assignment_type": "hybrid",
    "questions": [
        {
            "question_number": "1",
            "question_text": "Which of the following was NOT a major cause of the Industrial Revolution?",
            "question_type": "objective",
            "points": 5,
            "options": [
                "Agricultural Revolution",
                "Invention of the steam engine",
                "Discovery of electricity",
                "Expansion of colonial trade",
            ],
            "additional_notes": "Only one correct answer",
        },
        {
            "question_number": "2",
            "question_text": "Explain how the invention of the spinning jenny impacted textile production during"
            " the Industrial Revolution.",
            "question_type": "short_answer",
            "points": 10,
            "options": [],
            "additional_notes": "Response should be 3-5 sentences",
        },
        {
            "question_number": "3",
            "question_text": "Match the following inventions with their inventors:",
            "question_type": "objective",
            "points": 12,
            "options": ["Steam Engine", "Spinning Jenny", "Power Loom", "Cotton Gin"],
            "additional_notes": "Match each invention with: James Watt, James Hargreaves, Edmund Cartwright, "
            "Eli Whitney (respectively)",
        },
        {
            "question_number": "4",
            "question_text": "List three social consequences of urbanization during the Industrial Revolution.",
            "question_type": "short_answer",
            "points": 8,
            "options": [],
            "additional_notes": "Bullet points are acceptable",
        },
        {
            "question_number": "5",
            "question_text": "Analyze how the Industrial Revolution changed the nature of work and its impact "
            "on society. Discuss at least three significant changes and their long-term effects.",
            "question_type": "essay",
            "points": 15,
            "options": [],
            "additional_notes": "Response should be 2-3 paragraphs, double-spaced",
        },
    ],
    "extraction_confidence": "high",
    "potential_issues": [
        "Question 3 requires manual verification of correct answer matching",
        "Point distribution might need adjustment based on question complexity",
    ],
}


ASSIGNMENT_EXAMPLE = {
    "title": "World History - Industrial Revolution Quiz",
    "subject_name": "History",
    "instructions": "Answer all questions to the best of your ability.",
    "total_points": 50,
    "question_count": 5,
    "assignment_type": "HYBRID",
    "questions": [
        {
            "question_text": "Which of the following was NOT a major cause of the Industrial Revolution?",
            "question_type": "objective",
            "points": 5,
            "options": [
                "Agricultural Revolution",
                "Invention of the steam engine",
                "Discovery of electricity",
                "Expansion of colonial trade",
            ],
        }
    ],
}


@extend_schema_view(
    list=extend_schema(
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
            200: AssignmentSerializer(many=True),
            500: OpenApiResponse(description="Internal Server Error"),
        },
    ),
    create=extend_schema(
        summary="Create a new assignment",
        description="""Create a new assignment with the provided details.
        The assignment will be associated with the authenticated user as the creator.
        Questions and their details should be provided in the 'questions' field as a list of question objects.
        """,
        request=AssignmentSerializer,
        responses={
            201: OpenApiResponse(
                response=AssignmentSerializer,
                description="Assignment created successfully",
            ),
            400: OpenApiResponse(
                description="Invalid input. Missing required fields or invalid data format"
            ),
            # 401: OpenApiResponse(description="Authentication credentials were not provided"),
            # 403: OpenApiResponse(description="You do not have permission to perform this action")
        },
        examples=[
            OpenApiExample(
                "Create Assignment Example", value=ASSIGNMENT_EXAMPLE, request_only=True
            )
        ],
    ),
    retrieve=extend_schema(
        summary="Retrieve an assignment",
        description="Retrieve detailed information about a specific assignment by its ID.",
        responses={
            200: AssignmentSerializer,
            404: OpenApiResponse(description="Assignment not found"),
            500: OpenApiResponse(description="Internal Server Error"),
        },
    ),
    partial_update=extend_schema(
        summary="Partially update an assignment",
        description="Update one or more fields of an existing assignment.",
        request=AssignmentSerializer(partial=True),
        responses={
            200: AssignmentSerializer,
            400: OpenApiResponse(description="Invalid input"),
            # 401: OpenApiResponse(description="Authentication credentials were not provided"),
            # 403: OpenApiResponse(description="You do not have permission to perform this action"),
            404: OpenApiResponse(description="Assignment not found"),
        },
    ),
    destroy=extend_schema(
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
    parser_class = (MultiPartParser, FormParser)
    permission_classes = (AllowAny,)
    pagination_class = PageNumberPagination

    http_method_names = ["get", "post", "delete", "patch"]

    # def create(self, request):  # type: ignore
    #     data = request.data     # type: ignore
    #
    #     required_fields = [
    #         'assignment_name', 'subject_name', 'instructions', 'total_points',
    #         'question_count', 'assignment_type', 'questions'
    #     ]
    #
    #     for field in required_fields:
    #         if field not in data:
    #             return Response({"error": f"Missing required field: {field}"}, status=status.HTTP_400_BAD_REQUEST)
    #
    #     questions = data.get('questions', [])  # type: ignore
    #     if not isinstance(questions, list):
    #         return Response({"error": "Questions must be a list"}, status=status.HTTP_400_BAD_REQUEST)
    #
    #     for i, question in enumerate(questions, start=1): # type: ignore
    #         if not all(key in question for key in ('question_text', 'question_type', 'points', 'options',)):
    #             return Response(
    #                 {"error": f"Question {i} is missing required fields"},
    #                 status=status.HTTP_400_BAD_REQUEST
    #             )
    #
    #     if not isinstance(question['options'], list):  # type: ignore
    #         return Response(
    #             {"error": f"Question {i} options must be a list"},
    #             status=status.HTTP_400_BAD_REQUEST
    #         )
    #
    #
    #     # At this point the data is valid
    #
    #     assignment = Assignment.objects.create(
    #         title=data['assignment_name'],
    #         subject_name=data['subject_name'],
    #         instructions=data['instructions'],
    #         total_points=data['total_points'],
    #         question_count=data['question_count'],
    #         assignment_type=data['assignment_type'],
    #         questions=data['questions'],
    #     )
    #
    #     return Response({"message": "Assignment created successfully"}, status=status.HTTP_201_CREATED)

    # def retrieve(self, request, pk=None):
    #     try:
    #         # Get the assignment by primary key
    #         assignment = Assignment.objects.get(pk=pk)
    #
    #         response_data = {
    #             "id": assignment.id,
    #             "assignment_name": assignment.title,
    #             "subject_name": assignment.subject_name,
    #             "instructions": assignment.instructions,
    #             "total_points": assignment.total_points,
    #             "question_count": assignment.question_count,
    #             "assignment_type": assignment.assignment_type,
    #             "questions": assignment.extracted_data,
    #         }
    #
    #         return Response(response_data, status=status.HTTP_200_OK)
    #     except Assignment.DoesNotExist:
    #         return Response({"message": "Assignment not found"}, status=status.HTTP_404_NOT_FOUND)
    #     except Exception as e:
    #         return Response({"message": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    #
    #     try:
    #         assignment = Assignment.objects.get(pk=pk)
    #         assignment.delete()
    #         return Response({"message": "Assignment deleted successfully"}, status=status.HTTP_204_NO_CONTENT)
    #     except Assignment.DoesNotExist:
    #         return Response({"message": "Assignment not found"}, status=status.HTTP_404_NOT_FOUND)
    #     except Exception as e:
    #         return Response({"message": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR   )

    @extend_schema(
        summary="Upload assignment files (images or PDFs)",
        description="This endpoint allows users to upload one or more files. "
        "The files can be either images (JPEG, PNG, etc.) or PDFs. "
        "The system will process each file based on its type.",
        request={
            "multipart/form-data": {
                "type": "object",
                "properties": {
                    "assignments": {
                        "type": "array",
                        "items": {"type": "string", "format": "binary"},
                        "description": "A list of files to upload. You can select one or multiple files to upload.",
                    }
                },
            }
        },
        responses={
            201: {
                "description": "Files uploaded and processed successfully. Returns a list of structured assignment",
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {
                            "type": "string",
                            "description": "The title of the assignment.",
                        },
                        "description": {
                            "type": "string",
                            "description": "A brief description of the assignment.",
                        },
                        "assignment_type": {
                            "type": "string",
                            "description": "The type of the assignment (e.g., 'PDF' or 'Image').",
                        },
                        "section": {
                            "type": "string",
                            "description": "The section or category of the assignment.",
                        },
                        "questions": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "A list of questions extracted from the assignment.",
                        },
                        "page_count": {
                            "type": "integer",
                            "description": "The number of pages in the assignment.",
                        },
                    },
                    "example": RESPONSE_FORMAT_EXAMPLE,  # Use the defined example for clarity
                },
            },
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
        url_path="upload_assignment",
        url_name="upload_assignment",
    )
    def upload_assignment(self, request):
        # Access files using request.FILES
        files = request.FILES.getlist("assignments")

        if not files:
            return Response(
                {"error": "No files were uploaded"}, status=status.HTTP_400_BAD_REQUEST
            )

        results = []
        # uploaded_file_info = []
        image_formats = [
            "image/jpeg",
            "image/png",
            "image/gif",
            "image/webp",
        ]
        pdf_formats = "application/pdf"

        for uploaded_file in files:
            # Check if it's an instance of UploadedFile
            if not isinstance(uploaded_file, UploadedFile):
                return Response(
                    {"error": "Invalid file upload"}, status=status.HTTP_400_BAD_REQUEST
                )

            if uploaded_file.content_type in image_formats:
                try:
                    image = Image.open(uploaded_file)
                    questions = ocr_service.extract_with_pytessaract(image)
                    assignment_questions = ai_processor.extract_assignment_with_retry(
                        questions, max_retries=3
                    )
                    results.append(assignment_questions)
                except Exception as e:
                    return Response(
                        {"error": str(e)}, status=status.HTTP_400_BAD_REQUEST
                    )

            elif uploaded_file.content_type == pdf_formats:
                try:
                    pdf_service.set_uploaded_file(uploaded_file)
                    extracted_data = pdf_service.extract()

                    assignment_questions = ai_processor.extract_assignment_with_retry(
                        extracted_data["questions"], max_retries=3
                    )

                    results.append(assignment_questions)
                except Exception as e:
                    return Response(
                        {"error": str(e)}, status=status.HTTP_400_BAD_REQUEST
                    )
            else:
                return Response(
                    {
                        "error": f"File `{uploaded_file.name}` has an invalid format. Only images "
                        f"(JPEG, PNG, GIF, WebP) and PDFs are allowed."
                    },
                    status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                )

        return Response(results, status=status.HTTP_201_CREATED)
