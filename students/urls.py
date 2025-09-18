from rest_framework.routers import DefaultRouter

from students.views import StudentSubmissionViewSet

router = DefaultRouter()
router.register(
    r"student-submissions", StudentSubmissionViewSet, basename="student-submission"
)


urlpatterns = router.urls
