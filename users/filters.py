"""Enrollment filters for `CustomUserViewSet` that stay inside the requester's
tenancy.

DRF's `get_object()` runs `filter_queryset()`, so these apply to retrieve,
PATCH and DELETE, not only to the superadmin list. As plain
`filterset_fields` each `enrollments__*` lookup joined EVERY enrollment the
target account had - including ones with teachers the requester has nothing
to do with - which made `/users/<id>?enrollments__...=<value>` a yes/no oracle
on other tenants' enrollments (200 vs 404 on GET, 403 vs 404 on PATCH).

Here every lookup goes through the enrollments the requester is entitled to
see, so a foreign value answers exactly like a value that matches nothing.
The query parameter names are unchanged.
"""

import django_filters
from django.db.models import Exists, OuterRef, Q

from classrooms.models import EnrollmentStatusType, StudentCourse

from .models import CustomUser, UserTypes


class EnrollmentStatusInFilter(
    django_filters.BaseInFilter, django_filters.ChoiceFilter
):
    """`?enrollments__enrollment_status__in=A,B`, validated against choices."""


def visible_enrollments(user):
    """The enrollment rows `user` may filter accounts by.

    Mirrors `CustomUserViewSet.get_queryset()`: a superadmin (both flags) is
    platform-wide; an anonymous request sees nothing; a school admin sees
    enrollments in their school's courses; a teacher sees enrollments in
    their own courses; anyone else, only their own enrollments.
    """
    if not user or not user.is_authenticated:
        return Q(pk__in=[])
    if user.is_superuser and user.user_type == UserTypes.SUPER_ADMIN:
        return Q()
    if user.user_type == UserTypes.SCHOOL_ADMIN and user.school_id:
        return Q(course__teacher__school_id=user.school_id)
    if user.user_type == UserTypes.TEACHER:
        return Q(course__teacher=user)
    return Q(student=user)


class UserEnrollmentFilter(django_filters.FilterSet):
    enrollments__course = django_filters.UUIDFilter(method="filter_course")
    enrollments__course__isnull = django_filters.BooleanFilter(
        method="filter_course_isnull"
    )
    enrollments__course__session = django_filters.UUIDFilter(method="filter_session")
    enrollments__enrollment_status = django_filters.ChoiceFilter(
        choices=EnrollmentStatusType.choices, method="filter_status"
    )
    enrollments__enrollment_status__in = EnrollmentStatusInFilter(
        choices=EnrollmentStatusType.choices, method="filter_status_in"
    )

    class Meta:
        model = CustomUser
        # Single-valued forward lookups on the account itself: no fan-out
        # over enrollments, so the generated filters are safe as they are.
        fields = {"user_type": ["exact"], "school__name": ["exact"]}

    def _has_visible_enrollment(self, **lookup):
        return Exists(
            StudentCourse.objects.filter(
                visible_enrollments(self.request.user),
                student=OuterRef("pk"),
                **lookup,
            )
        )

    def filter_course(self, queryset, name, value):
        return queryset.filter(self._has_visible_enrollment(course_id=value))

    def filter_course_isnull(self, queryset, name, value):
        # StudentCourse.course is required, so "course is null" can only
        # mean "no enrollment at all" - of the ones this requester can see.
        has_one = self._has_visible_enrollment()
        return queryset.filter(~has_one if value else has_one)

    def filter_session(self, queryset, name, value):
        return queryset.filter(self._has_visible_enrollment(course__session_id=value))

    def filter_status(self, queryset, name, value):
        return queryset.filter(self._has_visible_enrollment(enrollment_status=value))

    def filter_status_in(self, queryset, name, value):
        return queryset.filter(
            self._has_visible_enrollment(enrollment_status__in=value)
        )
