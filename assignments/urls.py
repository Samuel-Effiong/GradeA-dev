from rest_framework.routers import SimpleRouter

from .views import AssignmentViewSet

router = SimpleRouter(trailing_slash=False)
router.register(r"assignments", AssignmentViewSet, basename="assignment")


urlpatterns = router.urls
