from rest_framework.routers import DefaultRouter

from classrooms.views import (  # ClassroomSettingsViewSet,; ClassroomViewSet,
    CourseViewSet,
    SchoolViewSet,
    SessionViewSet,
    StudentCourseViewSet,
    TopicViewSet,
)

router = DefaultRouter(trailing_slash=False)

router.register(r"schools", SchoolViewSet, basename="school")
router.register(r"sessions", SessionViewSet, basename="session")

router.register(r"course", CourseViewSet, basename="course")
router.register(r"student-course", StudentCourseViewSet, basename="student-course")
router.register(r"topics", TopicViewSet, basename="topic")


urlpatterns = router.urls
