from django.urls import path
from rest_framework.routers import SimpleRouter

from classrooms.views import (  # ClassroomSettingsViewSet,; ClassroomViewSet,
    CourseViewSet,
    SchoolViewSet,
    SessionViewSet,
    StudentCourseViewSet,
    TopicViewSet,
)
from classrooms.views_grade_categories import (
    CategoryPreviewView,
    CategoryTemplateDetailView,
    CategoryTemplateListView,
    CourseCategoriesView,
    ExcusedWorkView,
    GradeBreakdownView,
    GradingPolicyView,
)

router = SimpleRouter(trailing_slash=False)

router.register(r"schools", SchoolViewSet, basename="school")
router.register(r"sessions", SessionViewSet, basename="session")

router.register(r"course", CourseViewSet, basename="course")
router.register(r"student-course", StudentCourseViewSet, basename="student-course")
router.register(r"topics", TopicViewSet, basename="topic")


# Epic E2: dark until GRADE_CATEGORIES_ENABLED (classrooms/views_grade_categories.py).
grade_category_urls = [
    path(
        "course/<uuid:pk>/categories",
        CourseCategoriesView.as_view(),
        name="course-categories",
    ),
    path(
        "course/<uuid:pk>/categories/preview",
        CategoryPreviewView.as_view(),
        name="course-categories-preview",
    ),
    path(
        "course/<uuid:pk>/grade-breakdown",
        GradeBreakdownView.as_view(),
        name="course-grade-breakdown",
    ),
    path(
        "course/<uuid:pk>/grading-policy",
        GradingPolicyView.as_view(),
        name="course-grading-policy",
    ),
    path(
        "assignments/<uuid:pk>/excused",
        ExcusedWorkView.as_view(),
        name="assignment-excused",
    ),
    path(
        "category-templates",
        CategoryTemplateListView.as_view(),
        name="category-template-list",
    ),
    path(
        "category-templates/<uuid:pk>",
        CategoryTemplateDetailView.as_view(),
        name="category-template-detail",
    ),
]

urlpatterns = grade_category_urls + router.urls
