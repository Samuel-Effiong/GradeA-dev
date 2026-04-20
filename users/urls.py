from rest_framework.routers import DefaultRouter

from .views import (
    AuthViewSet,
    BetaWhitelistViewSet,
    CustomUserViewSet,
    SettingsViewSet,
    TaskViewSet,
    WaitlistViewSet,
)

router = DefaultRouter(trailing_slash=False)
router.register(r"users", CustomUserViewSet, basename="user")
router.register("auth", AuthViewSet, basename="auth")
router.register(r"tasks", TaskViewSet, basename="task")
router.register(r"users/settings", SettingsViewSet, basename="settings")
router.register(r"whitelist", BetaWhitelistViewSet, basename="whitelist")
router.register(r"waitlist", WaitlistViewSet, basename="waitlist")


urlpatterns = router.urls
