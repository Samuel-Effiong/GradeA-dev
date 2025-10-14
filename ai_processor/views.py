from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.viewsets import GenericViewSet

from ai_processor.models import ChatSession
from classrooms.models import Course


# Create your views here.
class AIProcessorViewSet(GenericViewSet):

    @extend_schema(
        tags=["AI Processor"],
    )
    @action(detail=False, methods=["post"])
    def generate_assignment_with_prompt(self, request):
        course_id = request.data.get("course_id")
        course = Course.objects.filter(id=course_id)

        if not course.exists():
            raise NotFound("Course not found")

        chat_session, created = ChatSession.objects.get_or_create(course=course.first())

        prompt = request.data.get("prompt")
        print(prompt, chat_session)
