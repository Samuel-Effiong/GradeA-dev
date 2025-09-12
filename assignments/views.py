from PIL import Image
from django.core.files.uploadedfile import UploadedFile
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from ai_processor.services import ai_processor, ocr_service, pdf_service

# from ai_processor.validators import AssignmentStructure

# from assignments.services import PDFService


# from assignments.services import PDFService

RESPONSE_FORMAT_EXAMPLE = {
    "title": "Introduction to Python",
    "description": "An assignment covering basic Python concepts.",
    "assignment_type": "PDF",
    "section": "Programming Fundamentals",
    "questions": [
        "What is a variable?",
        "Explain the difference between a list and a tuple.",
    ],
    "page_count": 5,
}


@extend_schema_view(
    list=extend_schema(responses={200: {"message": "List assignments"}}),
    create=extend_schema(
        request=None, responses={201: {"message": "Create assignment"}}
    ),
    retrieve=extend_schema(responses={200: {"message": "Retrieve assignment"}}),
)
class AssignmentViewSet(viewsets.ViewSet):
    parser_class = (MultiPartParser, FormParser)
    permission_classes = (AllowAny,)

    def list(self, request):
        return Response({"message": "List Assignments"})

    def create(self, request):
        return Response({"message": "Create Assignments"})

    def retrieve(self, request):
        return Response({"message": "Retrieve Assignments"})

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
