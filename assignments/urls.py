from rest_framework.routers import SimpleRouter

from .views import AssignmentGenerationSessionViewSet, AssignmentViewSet

router = SimpleRouter(trailing_slash=False)
router.register(r"assignments", AssignmentViewSet, basename="assignment")
router.register(
    r"assignment-generation-sessions",
    AssignmentGenerationSessionViewSet,
    basename="assignment-generation-session",
)


urlpatterns = router.urls
