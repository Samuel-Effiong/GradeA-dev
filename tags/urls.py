from django.urls import path
from rest_framework.routers import SimpleRouter

from .views import AssignmentTagsView, TagViewSet

router = SimpleRouter(trailing_slash=False)
router.register(r"tags", TagViewSet, basename="tag")

urlpatterns = router.urls + [
    path(
        "assignments/<uuid:pk>/tags",
        AssignmentTagsView.as_view(),
        name="assignment-tags",
    ),
]
