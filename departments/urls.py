from django.urls import path

from . import views

ID = "<str:department_id>"

urlpatterns = [
    path(
        "school-admin/departments",
        views.AdminDepartmentListView.as_view(),
        name="departments-admin-list",
    ),
    path(
        f"school-admin/departments/{ID}",
        views.AdminDepartmentDetailView.as_view(),
        name="departments-admin-detail",
    ),
    path(
        f"school-admin/departments/{ID}/members",
        views.AdminMembersView.as_view(),
        name="departments-admin-members",
    ),
    path(
        f"school-admin/departments/{ID}/members/<str:membership_id>",
        views.AdminMemberDetailView.as_view(),
        name="departments-admin-member-detail",
    ),
    path("departments", views.MyDepartmentsView.as_view(), name="departments-mine"),
    path(
        f"departments/{ID}/roster",
        views.RosterView.as_view(),
        name="departments-roster",
    ),
]
