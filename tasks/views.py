from django_q.tasks import result
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

# Create your views here.


class TaskViewSet(viewsets.ViewSet):
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["Tasks"],
        parameters=[
            OpenApiParameter(
                name="task_id",
                type=str,
                location=OpenApiParameter.QUERY,
                description="Task ID",
                required=True,
            )
        ],
    )
    @action(detail=False, methods=["get"])
    def get_tasks(self, request):
        task_id = request.query_params.get("task_id")

        item = result(task_id)
        return Response(item, status=status.HTTP_200_OK)
