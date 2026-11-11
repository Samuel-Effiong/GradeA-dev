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
from rest_framework.filters import OrderingFilter

from billing import intent_signals as signal_rules
from billing.models import BillingInterval, PlanCategory, PlanTier
from classrooms.models import (
    EnrollmentStatusType,
    StudentCourse,
    teacher_course_access_q,
)

from . import user_list
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
        # The same H-38 rule get_queryset() applies to a teacher: a course in
        # a school they were removed from is no longer visible, so it must
        # not be filterable either. Narrower than `course__teacher=user`.
        return teacher_course_access_q(user, prefix="course__")
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

    # User list sorting (owner 2026-11-11). Date bounds are inclusive; a
    # null date matches neither bound. Plan filters read the current plan
    # as billing resolves it (users.user_list).
    last_login_after = django_filters.IsoDateTimeFilter(method="filter_list_value")
    last_login_before = django_filters.IsoDateTimeFilter(method="filter_list_value")
    activated_after = django_filters.IsoDateTimeFilter(method="filter_list_value")
    activated_before = django_filters.IsoDateTimeFilter(method="filter_list_value")
    plan_tier = django_filters.ChoiceFilter(
        choices=PlanTier.choices, method="filter_list_value"
    )
    plan_category = django_filters.ChoiceFilter(
        choices=PlanCategory.choices, method="filter_list_value"
    )
    plan_interval = django_filters.ChoiceFilter(
        choices=BillingInterval.choices, method="filter_list_value"
    )
    has_plan = django_filters.BooleanFilter(method="filter_list_value")
    # Brief 4: the signals (billing.intent_signals), teachers only.
    signals_min = signal_rules.count_filter("filter_signal_value")
    at_80_percent = signal_rules.flag_filter("filter_signal_value")
    frequent_user = signal_rules.flag_filter("filter_signal_value")
    active_last_week = signal_rules.flag_filter("filter_signal_value")
    is_power_grader = signal_rules.flag_filter("filter_signal_value")

    #: param -> (value, lookup)
    LIST_FILTERS = {
        "last_login_after": ("last_login", "gte"),
        "last_login_before": ("last_login", "lte"),
        "activated_after": ("activated_at", "gte"),
        "activated_before": ("activated_at", "lte"),
        "plan_tier": ("plan_tier", "exact"),
        "plan_category": ("plan_category", "exact"),
        "plan_interval": ("plan_interval", "exact"),
    }

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

    def filter_list_value(self, queryset, name, value):
        """Super admin only. filter_queryset also runs under get_object(),
        and a teacher or school admin can retrieve some accounts: a filter on
        another account's plan or sign-in dates would be a 200-vs-404
        yes/no oracle there. For anyone else these filters do nothing."""
        user = getattr(self.request, "user", None)
        if not (user and user.is_superuser and user.user_type == UserTypes.SUPER_ADMIN):
            return queryset
        if name == "has_plan":
            queryset = user_list.ensure(queryset, "current_plan_id")
            return queryset.filter(current_plan_id__isnull=not value)
        field, lookup = self.LIST_FILTERS[name]
        if field != "last_login":
            queryset = user_list.ensure(queryset, field)
        return queryset.filter(**{f"{field}__{lookup}": value})

    def filter_signal_value(self, queryset, name, value):
        """The same super admin gate as filter_list_value."""
        user = getattr(self.request, "user", None)
        if not (user and user.is_superuser and user.user_type == UserTypes.SUPER_ADMIN):
            return queryset
        queryset = user_list.ensure_signals(
            queryset, signal_rules.request_now(self.request)
        )
        return signal_rules.filter_signals(queryset, name, value)


class UserListOrderingFilter(OrderingFilter):
    """DRF's OrderingFilter with the list's computed sort keys: nulls last
    both ways and id as the tiebreaker (users.user_list.apply_ordering).
    Unknown keys are still dropped by remove_invalid_fields."""

    def filter_queryset(self, request, queryset, view):
        return user_list.apply_ordering(
            queryset,
            self.get_ordering(request, queryset, view) or [],
            now=signal_rules.request_now(request),
        )
