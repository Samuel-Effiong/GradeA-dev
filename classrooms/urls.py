from rest_framework.routers import SimpleRouter

from classrooms.views import (  # ClassroomSettingsViewSet,; ClassroomViewSet,
    CourseViewSet,
    SchoolViewSet,
    SessionViewSet,
    StudentCourseViewSet,
    TopicViewSet,
)

router = SimpleRouter(trailing_slash=False)

router.register(r"schools", SchoolViewSet, basename="school")
router.register(r"sessions", SessionViewSet, basename="session")

router.register(r"course", CourseViewSet, basename="course")
router.register(r"student-course", StudentCourseViewSet, basename="student-course")
router.register(r"topics", TopicViewSet, basename="topic")


urlpatterns = router.urls
