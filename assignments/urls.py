from django.urls import path
from rest_framework.routers import SimpleRouter

from .views import AssignmentGenerationSessionViewSet, AssignmentViewSet
from .views_copy import AssignmentBulkCopyView, AssignmentCopyView

router = SimpleRouter(trailing_slash=False)
router.register(r"assignments", AssignmentViewSet, basename="assignment")
router.register(
    r"assignment-generation-sessions",
    AssignmentGenerationSessionViewSet,
    basename="assignment-generation-session",
)


# Epic E2: dark until ASSIGNMENT_COPY_ENABLED (assignments/views_copy.py).
# Before the router, so "bulk-copy" is never read as an assignment id.
copy_urls = [
    path(
        "assignments/bulk-copy",
        AssignmentBulkCopyView.as_view(),
        name="assignment-bulk-copy",
    ),
    path(
        "assignments/<uuid:pk>/copy",
        AssignmentCopyView.as_view(),
        name="assignment-copy",
    ),
]

urlpatterns = copy_urls + router.urls
