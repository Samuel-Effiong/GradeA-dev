from rest_framework.routers import DefaultRouter

from .views import AuthViewSet, CustomUserViewSet, SettingsViewSet, TaskViewSet

router = DefaultRouter(trailing_slash=False)
router.register(r"users", CustomUserViewSet, basename="user")
router.register("auth", AuthViewSet, basename="auth")
router.register(r"tasks", TaskViewSet, basename="task")
router.register(r"users/settings", SettingsViewSet, basename="settings")


urlpatterns = router.urls
