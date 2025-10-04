from rest_framework.routers import DefaultRouter

from tasks.views import TaskViewSet

router = DefaultRouter()
register = router.register(r"tasks", TaskViewSet, basename="task")


urlpatterns = router.urls
