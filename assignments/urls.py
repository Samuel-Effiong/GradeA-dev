from rest_framework.routers import DefaultRouter

from .views import AssignmentViewSet, RubricViewSet

router = DefaultRouter(trailing_slash=False)
router.register(r"assignments", AssignmentViewSet, basename="assignment")
router.register(r"rubrics", RubricViewSet, basename="rubric")

urlpatterns = router.urls
