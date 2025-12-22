from django.views.generic import TemplateView
from rest_framework.decorators import action

# from classrooms.models import Course


class DashboardView(TemplateView):

    @action(detail=False, methods=["get"], url_path="stats")
    def overview(self, request, *args, **kwargs):
        # Implementation for stats endpoint
        # active_course = Course.objects.filter(is_active=True).count()

        print("Love God with the whole of your heart")

        pass
