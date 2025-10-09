from rest_framework.routers import DefaultRouter

from classrooms.views import (  # ClassroomSettingsViewSet,; ClassroomViewSet,
    CourseViewSet,
    SessionViewSet,
    StudentCourseViewSet,
)

router = DefaultRouter()

router.register(r"sessions", SessionViewSet, basename="session")
# router.register(r"classrooms", ClassroomViewSet, basename="classroom")
# router.register(
#     r"classroom-settings", ClassroomSettingsViewSet, basename="classroom-setting"
# )
router.register(r"course", CourseViewSet, basename="course")
router.register(r"student-course", StudentCourseViewSet, basename="student-course")


urlpatterns = router.urls
