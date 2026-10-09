from django.urls import path

from .views import SchoolAdminAuditEventListView, SuperAdminAuditEventListView

urlpatterns = [
    path(
        "super-admin/audit/events",
        SuperAdminAuditEventListView.as_view(),
        name="super-admin-audit-events",
    ),
    path(
        "school-admin/audit/events",
        SchoolAdminAuditEventListView.as_view(),
        name="school-admin-audit-events",
    ),
]
