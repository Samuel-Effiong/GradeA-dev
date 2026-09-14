from rest_framework.routers import DefaultRouter

from .views import (
    SchoolAdminDashboardView,
    StudentAdminDashboardView,
    SuperAdminDashboardView,
    TeacherAdminDashboardView,
)

router = DefaultRouter(trailing_slash=False)

router.register(r"super-admin", SuperAdminDashboardView, basename="dashboard")
router.register(r"school-admin", SchoolAdminDashboardView, basename="school-admin")
router.register(r"teacher-admin", TeacherAdminDashboardView, basename="teacher-admin")
router.register(r"student-admin", StudentAdminDashboardView, basename="student")

urlpatterns = router.urls
