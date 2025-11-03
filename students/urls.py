from rest_framework.routers import DefaultRouter

from students.views import StudentSubmissionViewSet

router = DefaultRouter()
router.register(r"submissions", StudentSubmissionViewSet, basename="student-submission")


urlpatterns = router.urls
