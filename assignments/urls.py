from rest_framework.routers import DefaultRouter

from .views import AssignmentViewSet

router = DefaultRouter(trailing_slash=False)
router.register(r"assignments", AssignmentViewSet, basename="assignment")


urlpatterns = router.urls
