from django.urls import path
from rest_framework.routers import SimpleRouter

from students.views import StudentSubmissionViewSet
from students.views_data_erase import StudentDataEraseView
from students.views_data_export import StudentDataExportView

router = SimpleRouter(trailing_slash=False)
router.register(r"submissions", StudentSubmissionViewSet, basename="student-submission")


urlpatterns = router.urls + [
    # NFR-CMP-04: dark until STUDENT_DATA_TOOLS_ENABLED.
    path(
        "students/<uuid:pk>/data-export",
        StudentDataExportView.as_view(),
        name="student-data-export",
    ),
    path(
        "students/<uuid:pk>/erase",
        StudentDataEraseView.as_view(),
        name="student-data-erase",
    ),
]
