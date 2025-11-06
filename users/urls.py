from rest_framework.routers import DefaultRouter

from .views import AuthViewSet, CustomUserViewSet

router = DefaultRouter()
router.register(r"users", CustomUserViewSet, basename="user")
router.register("auth", AuthViewSet, basename="auth")


urlpatterns = router.urls
