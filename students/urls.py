from rest_framework.routers import SimpleRouter

from students.views import StudentSubmissionViewSet

router = SimpleRouter(trailing_slash=False)
router.register(r"submissions", StudentSubmissionViewSet, basename="student-submission")


urlpatterns = router.urls
