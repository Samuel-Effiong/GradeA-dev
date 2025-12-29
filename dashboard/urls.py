from rest_framework.routers import DefaultRouter

from .views import SchoolAdminDashboardView, SuperAdminDashboardView

# app_name = "admin"

router = DefaultRouter(trailing_slash=False)

router.register(r"super-admin", SuperAdminDashboardView, basename="dashboard")
router.register(r"school-admin", SchoolAdminDashboardView, basename="teacher")

# router.register(r"students", StudentAdmin, basename="student")


urlpatterns = router.urls
