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
from rest_framework.exceptions import NotAcceptable, NotFound
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from ai_processor.models import ChatMessage, ChatSession, RoleType
from ai_processor.services import ai_processor, ocr_service, pdf_service
from classrooms.models import Course
from students.serializers import StudentSubmissionSerializer

from .models import Assignment, Rubric
from .serializers import AssignmentSerializer, RubricSerializer

# from ai_processor.validators import AssignmentStructure

# from assignments.services import PDFService


# from assignments.services import PDFService

RESPONSE_FORMAT_EXAMPLE = {
    "title": "World History - Industrial Revolution Quiz",
    "subject_name": "History",
    "instructions": "Answer all questions to the best of your ability. Write your answers in the spaces provided. "
    "For multiple choice questions, select the best answer. Use complete sentences for essay questions.",
    "total_points": 50,
    "question_count": 5,
    "assignment_type": "HYBRID",
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
    "course": 0,
    "title": "World History - Industrial Revolution Quiz",
    "instructions": "Answer all questions to the best of your ability.",
    "total_points": 50,
    "question_count": 5,
    "assignment_type": "HYBRID",
    "created_by": 0,
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
        tags=["04 Assignments"],
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
        tags=["04 Assignments"],
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
        # examples=[
        #     OpenApiExample(
        #         "Create Assignment Example", value=ASSIGNMENT_EXAMPLE, request_only=True
        #     )
        # ],
    ),
    retrieve=extend_schema(
        tags=["04 Assignments"],
        summary="Retrieve an assignment",
        description="Retrieve detailed information about a specific assignment by its ID.",
        responses={
            200: AssignmentSerializer,
            404: OpenApiResponse(description="Assignment not found"),
            500: OpenApiResponse(description="Internal Server Error"),
        },
    ),
    partial_update=extend_schema(
        tags=["04 Assignments"],
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
        tags=["04 Assignments"],
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
    permission_classes = (AllowAny,)
    pagination_class = PageNumberPagination

    http_method_names = ["get", "head", "post", "delete", "patch", "options"]

    def get_queryset(self):
        user = self.request.user
        return Assignment.objects.filter(course__teacher=user)

    @extend_schema(
        tags=["04 Assignments"],
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
                        "course": {
                            "type": "string",
                            "description": "The course or category of the assignment.",
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
                    questions = ocr_service.extract_with_paddle(image)
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

    @extend_schema(
        tags=["05 Rubrics"],
        operation_id="upload_rubric",
        summary="Upload rubric file (image or PDF)",
        description="""
        Upload rubric file (PDF or images) for processing.
        The endpoint accepts file and processes it to extract rubric data.
        The extracted data will be associated with the specified assignment.
        """,
        request={
            "multipart/form-data": {
                "type": "object",
                "properties": {
                    "rubric": {
                        "type": "string",
                        "format": "binary",
                        "description": "Rubric file (PDF, JPEG, PNG, GIF, or WebP)",
                    }
                },
                "required": ["rubric"],
            }
        },
        responses={
            201: OpenApiResponse(
                response=RubricSerializer,
                description="Rubric files processed successfully",
                examples=[
                    OpenApiExample(
                        name="Rubric Example",
                        value={
                            "assignment_id": 1,
                            "criteria": [
                                {
                                    "id": 1,
                                    "question": "Explain three main causes of climate change.",
                                    "max_points": 15,
                                    "model_answer": "The main causes are greenhouse gas emissions from burning fossil"
                                    " fuels, deforestation, and industrial activities that release "
                                    "carbon dioxide and methane.",
                                    "scoring_levels": [
                                        {
                                            "level": "Excellent",
                                            "points": 15,
                                            "description": "Mentions at least 3 causes with detailed explanations "
                                            "and examples.",
                                        },
                                        {
                                            "level": "Good",
                                            "points": 10,
                                            "description": "Mentions at least 2 causes with moderate explanation.",
                                        },
                                        {
                                            "level": "Fair",
                                            "points": 5,
                                            "description": "Mentions only 1 cause with limited detail.",
                                        },
                                        {
                                            "level": "Poor",
                                            "points": 0,
                                            "description": "Fails to identify valid causes or gives irrelevant "
                                            "answers.",
                                        },
                                    ],
                                }
                            ],
                            "rubric_analysis": {
                                "extraction_confidence": 0.0,
                                "text_quality": "high/medium/low",
                                "total_questions_found": 0,
                                "total_possible_points": 0,
                                "detected_metadata": {
                                    "title": "",
                                    "subject": "",
                                    "grade_level": "",
                                    "instructor": "",
                                    "course": "",
                                    "date": "",
                                    "instructions": "",
                                },
                            },
                            "rubric_quality_assessment": {
                                "clarity_score": 0.0,
                                "completeness_score": 0.0,
                                "consistency_score": 0.0,
                                "alignment_score": 0.0,
                                "overall_quality": "excellent/good/fair/poor",
                            },
                            "identified_issues": [
                                {
                                    "issue_type": "",
                                    "severity": "high/medium/low",
                                    "description": "",
                                    "suggestion": "",
                                }
                            ],
                            "strengths": [],
                            "improvement_recommendations": [],
                            "processing_notes": [],
                            "assumptions_made": [],
                            "unclear_sections": [],
                        },
                        response_only=True,
                    )
                ],
            ),
            400: OpenApiResponse(
                description="Bad Request",
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
        url_path=r"(?P<assignment_id>[-\w]+)/upload_rubric",
        url_name="upload_rubric",
    )
    def upload_rubric(self, request, assignment_id=None):
        if not Assignment.objects.filter(pk=assignment_id).exists():
            raise NotFound("No Assignment found with this ID.")

        files = request.FILES.getlist("rubric")

        if not files:
            return Response(
                {"error": "No files were uploaded"}, status=status.HTTP_400_BAD_REQUEST
            )

        if len(files) > 1:
            raise NotAcceptable(
                "Only one file can be uploaded at a time. Please try again."
            )

        image_formats = [
            "image/jpeg",
            "image/png",
            "image/gif",
            "image/webp",
        ]

        pdf_formats = "application/pdf"

        rubric = None

        for uploaded_file in files:
            if not isinstance(uploaded_file, UploadedFile):
                return Response(
                    {"error": "Invalid file upload"}, status=status.HTTP_400_BAD_REQUEST
                )

            if uploaded_file.content_type in image_formats:
                try:
                    image = Image.open(uploaded_file)
                    rubric = ocr_service.extract_with_pytessaract(image)

                    rubric = ai_processor.extract_rubric_with_retry(
                        rubric, max_retries=3
                    )
                except Exception as e:
                    return Response(
                        {"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR
                    )
            elif uploaded_file.content_type == pdf_formats:
                try:
                    pdf_service.set_uploaded_file(uploaded_file)
                    extracted_data = pdf_service.extract()

                    rubric = ai_processor.extract_rubric_with_retry(
                        extracted_data["questions"], max_retries=3
                    )
                except Exception as e:
                    return Response(
                        {"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR
                    )
            else:
                return Response(
                    {
                        "error": f"File `{uploaded_file.name}` has an invalid format. Only images "
                        f"(JPEG, PNG, GIF, WebP) and PDFs are allowed."
                    },
                    status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                )

        if rubric is not None:
            rubric["assignment"] = assignment_id
        return Response(rubric, status=status.HTTP_201_CREATED)

    @extend_schema(
        tags=["04 Assignments"],
        summary="Submit an assignment",
        description="Students use this endpoint to submit their answers to an assignment. "
        "The submission can be saved as a draft or finalized.",
        request={
            "multipart/form-data": {
                "type": "object",
                "properties": {
                    "answers": {
                        "type": "string",
                        "format": "binary",
                        "description": "Assignment to be submitted",
                    },
                },
                "required": ["answers"],
            }
        },
        responses={
            201: StudentSubmissionSerializer,
            400: OpenApiResponse(
                description="Bad request - invalid data",
                examples=[{"error": "Invalid submission data"}],
            ),
            403: OpenApiResponse(
                description="Not authorized",
                examples=[
                    {"error": "You do not have permission to submit to this assignment"}
                ],
            ),
            404: OpenApiResponse(
                description="Assignment not found",
                examples=[{"error": "Assignment not found"}],
            ),
            409: OpenApiResponse(
                description="Conflict - already submitted",
                examples=[{"error": "You have already submitted this assignment"}],
            ),
        },
    )
    @action(
        detail=False,
        methods=["POST"],
        url_path=r"(?P<assignment_id>[-\w]+)/submit_assignment",
        url_name="submit_assignment",
    )
    def submit_assignment(self, request, assignment_id=None):
        if not Assignment.objects.filter(pk=assignment_id).exists():
            raise NotFound("No Assignment found with this ID.")

    @extend_schema(tags=["04 Assignments"])
    @action(
        detail=False,
        methods=["POST"],
        url_path=r"generate_assignment/(?P<course_id>[-\w]+)",
        url_name="generate_assignment",
    )
    def generate_assignment_from_prompt(self, request, course_id):
        """
        Generate a new assignment based on text prompts.

        This endpoint accepts a text prompt and generates an assignment with questions
        and answers using AI processing.
        """

        course = Course.objects.filter(id=course_id)

        if not course.exists():
            raise NotFound("No Cours found with this ID.")

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
            return Response(
                {"error": "Prompt is required to generate an assignment."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:

            generated_assignment = (
                ai_processor.generate_assignment_from_prompt_with_retry(
                    prompt, chat_history=messages, max_retries=3
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
            return Response(generated_assignment, status=status.HTTP_201_CREATED)
        except Exception as e:
            return Response(
                {"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


@extend_schema_view(
    create=extend_schema(
        tags=["05 Rubrics"],
        summary="Create a new rubric",
        description="""
        Create a new rubric with the provided criteria.

        A rubric consists of multiple criteria, each with its own scoring levels.
        The sum of max_points across all criteria must match the assignment's total_points.
        """,
        request=RubricSerializer,
        responses={
            201: OpenApiResponse(
                response=RubricSerializer, description="Rubric created successfully"
            ),
            400: OpenApiResponse(description="Invalid input data"),
            500: OpenApiResponse(description="Internal server error"),
        },
        examples=[
            OpenApiExample(
                "Create Rubric Example",
                value={
                    "assignment": 1,
                    "criteria": [
                        {
                            "question": "Code Quality",
                            "max_points": 30,
                            "model_answer": "The code should be well-structured, properly formatted, and follow"
                            " best practices.",
                            "scoring_levels": [
                                {
                                    "level": "Excellent",
                                    "points": 30,
                                    "description": "Code is exceptionally well-organized, follows all best practices,"
                                    " and demonstrates advanced techniques.",
                                },
                                {
                                    "level": "Good",
                                    "points": 20,
                                    "description": "Code is well-structured with minor issues that don't affect "
                                    "functionality.",
                                },
                                {
                                    "level": "Needs Improvement",
                                    "points": 10,
                                    "description": "Code is functional but has significant structural or style issues.",
                                },
                            ],
                        },
                        {
                            "question": "Functionality",
                            "max_points": 40,
                            "model_answer": "The code should correctly implement all required functionality.",
                            "scoring_levels": [
                                {
                                    "level": "Excellent",
                                    "points": 40,
                                    "description": "All functionality is implemented correctly and efficiently.",
                                },
                                {
                                    "level": "Good",
                                    "points": 30,
                                    "description": "Most functionality is implemented with minor issues.",
                                },
                                {
                                    "level": "Needs Improvement",
                                    "points": 15,
                                    "description": "Basic functionality is present but with significant issues.",
                                },
                            ],
                        },
                    ],
                },
                request_only=True,
                status_codes=["201"],
            )
        ],
    ),
    list=extend_schema(
        tags=["05 Rubrics"],
        summary="List all rubrics",
        description="Retrieve a paginated list of all rubrics in the system.",
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
            200: RubricSerializer(many=True),
            500: OpenApiResponse(description="Internal Server Error"),
        },
    ),
    retrieve=extend_schema(
        tags=["05 Rubrics"],
        summary="Retrieve a rubric",
        description="Retrieve detailed information about a specific rubric by its ID.",
        responses={
            200: RubricSerializer,
            404: OpenApiResponse(description="Rubric not found"),
            500: OpenApiResponse(description="Internal Server Error"),
        },
    ),
    partial_update=extend_schema(
        tags=["05 Rubrics"],
        summary="Update a rubric",
        description="Update one or more fields of an existing rubric.",
        request=RubricSerializer(),
        responses={
            200: RubricSerializer,
            400: OpenApiResponse(description="Invalid input"),
            404: OpenApiResponse(description="Rubric not found"),
            500: OpenApiResponse(description="Internal Server Error"),
        },
        examples=[
            OpenApiExample(
                "Update Rubric Example",
                value={
                    "assignment": 1,
                    "criteria": [
                        {
                            "id": 1,
                            "question": "Updated question text",
                            "max_points": 15,
                            "model_answer": "Updated model answer",
                            "scoring_levels": [
                                {
                                    "level": "Excellent",
                                    "points": 15,
                                    "description": "Updated description",
                                }
                            ],
                        }
                    ],
                },
                request_only=True,
            )
        ],
    ),
    destroy=extend_schema(
        tags=["05 Rubrics"],
        summary="Delete a rubric",
        description="Delete a rubric by ID. This action cannot be undone.",
        responses={
            204: OpenApiResponse(description="Rubric deleted successfully"),
            404: OpenApiResponse(description="Rubric not found"),
            500: OpenApiResponse(description="Internal Server Error"),
        },
    ),
)
class RubricViewSet(viewsets.ModelViewSet):
    """
    A viewset for viewing and editing rubrics.
    """

    queryset = Rubric.objects.all()
    serializer_class = RubricSerializer
    permission_classes = (IsAuthenticated,)
    pagination_class = PageNumberPagination
    http_method_names = ["get", "head", "post", "delete", "patch", "options"]

    def get_queryset(self):
        user = self.request.user
        return Rubric.objects.filter(assignment__course__teacher=user)

    @extend_schema(
        tags=["05 Rubrics"],
        methods=["GET"],
        summary="Retrieve rubric by assignment ID",
        description="Retrieve a rubric by its associated assignment ID.",
        parameters=[
            OpenApiParameter(
                name="assignment_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="ID of the assignment to retrieve rubric for",
                required=True,
            ),
        ],
        responses={
            200: RubricSerializer,
            404: OpenApiResponse(
                description="Rubric not found for the given assignment"
            ),
            500: OpenApiResponse(description="Internal Server Error"),
        },
    )
    @action(detail=False, methods=["get"])
    def by_assignment(self, request):
        """
        Retrieve rubric by assignment ID.
        """
        assignment_id = request.query_params.get("assignment_id")
        if not assignment_id:
            return Response(
                {"error": "assignment_id parameter is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            rubric = self.queryset.get(assignment_id=assignment_id)
            serializer = self.get_serializer(rubric)
            return Response(serializer.data)
        except Rubric.DoesNotExist:
            return Response(
                {"error": "No rubric found for the given assignment"},
                status=status.HTTP_404_NOT_FOUND,
            )
