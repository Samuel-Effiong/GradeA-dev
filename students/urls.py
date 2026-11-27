from django.urls import path
from rest_framework.routers import SimpleRouter

from students.upload_quote_views import (
    UploadQuoteCollection,
    UploadQuoteConfirm,
    UploadQuoteDetail,
    UploadQuoteFiles,
)
from students.views import StudentSubmissionViewSet
from students.views_data_erase import StudentDataEraseView
from students.views_data_export import StudentDataExportView

router = SimpleRouter(trailing_slash=False)
router.register(r"submissions", StudentSubmissionViewSet, basename="student-submission")


urlpatterns = router.urls + [
    # Upload quote: dark until UPLOAD_QUOTES_ENABLED (students/upload_quote_views.py).
    path(
        "submissions-credit-estimate/",
        UploadQuoteCollection.as_view(),
        name="upload-quotes-collection",
    ),
    path(
        "submissions-credit-estimate/<str:pk>",
        UploadQuoteDetail.as_view(),
        name="upload-quote-detail",
    ),
    path(
        "submissions-credit-estimate/<str:pk>/files",
        UploadQuoteFiles.as_view(),
        name="upload-quote-files",
    ),
    path(
        "submissions-credit-estimate/<str:pk>/confirm",
        UploadQuoteConfirm.as_view(),
        name="upload-quote-confirm",
    ),
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
