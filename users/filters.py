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
from django import forms
from django.core.validators import EMPTY_VALUES
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


#: param -> the enrollment lookup it adds. Given together, they must match
#: ONE enrollment (owner, brief 6, 2026-11-11): "course A + WITHDRAWN" keeps
#: a student withdrawn from A, not one withdrawn only from course B.
ENROLLMENT_LOOKUPS = {
    "enrollments__course": "course_id",
    "enrollments__course__session": "course__session_id",
    "enrollments__enrollment_status": "enrollment_status",
}


class WholeNumberFilter(django_filters.NumberFilter):
    """A whole number: 1.5 or abc is a 400, like any other bad filter value."""

    field_class = forms.IntegerField


class UserEnrollmentFilter(django_filters.FilterSet):
    enrollments__course = django_filters.UUIDFilter(method="filter_enrollment")
    enrollments__course__isnull = django_filters.BooleanFilter(
        method="filter_course_isnull"
    )
    enrollments__course__session = django_filters.UUIDFilter(method="filter_enrollment")
    enrollments__enrollment_status = django_filters.ChoiceFilter(
        choices=EnrollmentStatusType.choices, method="filter_enrollment"
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
    # Usage ranges (owner 2026-11-12): whole numbers, both ends inclusive,
    # compared with the list's own values (users.user_list), in the credits
    # users see.
    credit_usage_min = WholeNumberFilter(method="filter_usage_range")
    credit_usage_max = WholeNumberFilter(method="filter_usage_range")
    credits_left_min = WholeNumberFilter(method="filter_usage_range")
    credits_left_max = WholeNumberFilter(method="filter_usage_range")
    papers_graded_min = WholeNumberFilter(method="filter_usage_range")
    papers_graded_max = WholeNumberFilter(method="filter_usage_range")
    assignments_created_min = WholeNumberFilter(method="filter_usage_range")
    assignments_created_max = WholeNumberFilter(method="filter_usage_range")
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
        "credit_usage_min": ("credits_used", "gte"),
        "credit_usage_max": ("credits_used", "lte"),
        "credits_left_min": ("credits_left", "gte"),
        "credits_left_max": ("credits_left", "lte"),
        "papers_graded_min": ("papers_graded", "gte"),
        "papers_graded_max": ("papers_graded", "lte"),
        "assignments_created_min": ("assignments_created", "gte"),
        "assignments_created_max": ("assignments_created", "lte"),
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

    def filter_queryset(self, queryset):
        """django-filter's loop, except that the ENROLLMENT_LOOKUPS filters
        go into one Exists, so they match the same visible enrollment."""
        data = self.form.cleaned_data
        lookup = {
            ENROLLMENT_LOOKUPS[name]: value
            for name, value in data.items()
            if name in ENROLLMENT_LOOKUPS and value not in EMPTY_VALUES
        }
        if lookup:
            queryset = queryset.filter(self._has_visible_enrollment(**lookup))
        for name, value in data.items():
            if name not in ENROLLMENT_LOOKUPS:
                queryset = self.filters[name].filter(queryset, value)
        return queryset

    def filter_enrollment(self, queryset, name, value):
        """One enrollment filter on its own. filter_queryset does not call
        this (it merges them); kept for any direct Filter.filter() call."""
        return queryset.filter(
            self._has_visible_enrollment(**{ENROLLMENT_LOOKUPS[name]: value})
        )

    def filter_course_isnull(self, queryset, name, value):
        # StudentCourse.course is required, so "course is null" can only
        # mean "no enrollment at all" - of the ones this requester can see.
        has_one = self._has_visible_enrollment()
        return queryset.filter(~has_one if value else has_one)

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

    def filter_usage_range(self, queryset, name, value: int):
        """The usage ranges are filter_list_value's. The `value: int` hint is
        what makes Swagger show an integer, not a generic number."""
        return self.filter_list_value(queryset, name, value)

    def filter_signal_value(self, queryset, name, value):
        """The same super admin gate as filter_list_value."""
        user = getattr(self.request, "user", None)
        if not (user and user.is_superuser and user.user_type == UserTypes.SUPER_ADMIN):
            return queryset
        queryset = user_list.ensure_signals(
            queryset, signal_rules.request_now(self.request)
        )
        return signal_rules.filter_signals(queryset, name, value)


#: Swagger text for every filter above (brief 6, owner 2026-11-11: plain
#: words). Docs only: help_text reaches drf-spectacular and changes no
#: filtering. Each rule is the code's own (users.user_list,
#: billing.intent_signals).
_SUPER_ADMIN_ONLY = "Works for super admins only; for anyone else it is ignored."
_YOUR_ENROLLMENTS = "Only enrollments you are allowed to see count."
_TOGETHER = (
    "If you give two or three of enrollments__course, "
    "enrollments__course__session and enrollments__enrollment_status, they "
    "must all match the same enrollment. For example, a course and WITHDRAWN keeps students who "
    "withdrew from that course, not students who withdrew from another one."
)
_DATE_ONLY = (
    "A date with no time means the very start of that day (midnight UTC), so "
    "{example} leaves out that day itself."
)
_EMPTY = "Left empty, it does nothing."
_START_OF_DAY = (
    "A date with no time means the very start of that day (midnight UTC), so "
    "that whole day is included."
)
_TEACHERS_ONLY = "Only teachers can have signals; every other user counts as false."
FILTER_HELP = {
    "user_type": "Keeps only users of this type.",
    "school__name": (
        "Keeps only users whose school has exactly this name, with the same "
        "capital letters."
    ),
    "enrollments__course": (
        "A course ID. Keeps users who are enrolled in this course. "
        f"{_YOUR_ENROLLMENTS} {_TOGETHER}"
    ),
    "enrollments__course__isnull": (
        "true: keeps users who have no enrollment you can see. false: keeps "
        f"users who have at least one. {_EMPTY}"
    ),
    "enrollments__course__session": (
        "A session ID. Keeps users enrolled in any course of this session. "
        f"{_YOUR_ENROLLMENTS} {_TOGETHER}"
    ),
    "enrollments__enrollment_status": (
        "Keeps users who have an enrollment with this status. "
        f"{_YOUR_ENROLLMENTS} {_TOGETHER}"
    ),
    "last_login_after": (
        "Keeps users who last signed in at or after this date and time; the "
        "time you give is included. "
        f"{_START_OF_DAY} Users who never signed in are left out. "
        f"{_SUPER_ADMIN_ONLY}"
    ),
    "last_login_before": (
        "Keeps users who last signed in at or before this date and time; the "
        "time you give is included. "
        + _DATE_ONLY.format(example="`last_login_before=2026-11-01`")
        + f" Users who never signed in are left out. {_SUPER_ADMIN_ONLY}"
    ),
    "activated_after": (
        "Keeps users who confirmed their email address at or after this date "
        "and time; the time you give is included. "
        f"{_START_OF_DAY} Users who have not confirmed are left out. "
        f"{_SUPER_ADMIN_ONLY}"
    ),
    "activated_before": (
        "Keeps users who confirmed their email address at or before this "
        "date and time; the time you give is included. "
        + _DATE_ONLY.format(example="`activated_before=2026-11-01`")
        + f" Users who have not confirmed are left out. {_SUPER_ADMIN_ONLY}"
    ),
    "credit_usage_min": (
        "Keeps users who have used at least this many credits in total, after "
        f"refunds. Users who have used none count as 0. {_SUPER_ADMIN_ONLY}"
    ),
    "credit_usage_max": (
        "Keeps users who have used at most this many credits in total, after "
        f"refunds. Users who have used none count as 0. {_SUPER_ADMIN_ONLY}"
    ),
    "credits_left_min": (
        "Keeps users with at least this many credits left. Users without a "
        f"credit wallet are left out. {_SUPER_ADMIN_ONLY}"
    ),
    "credits_left_max": (
        "Keeps users with at most this many credits left. Users without a "
        f"credit wallet are left out. {_SUPER_ADMIN_ONLY}"
    ),
    "papers_graded_min": (
        "Keeps teachers who have had at least this many papers graded by the "
        f"AI. Teachers who have graded none count as 0. {_SUPER_ADMIN_ONLY}"
    ),
    "papers_graded_max": (
        "Keeps teachers who have had at most this many papers graded by the "
        f"AI. Teachers who have graded none count as 0. {_SUPER_ADMIN_ONLY}"
    ),
    "assignments_created_min": (
        "Keeps teachers who have created at least this many assignments. "
        f"Teachers who have created none count as 0. {_SUPER_ADMIN_ONLY}"
    ),
    "assignments_created_max": (
        "Keeps teachers who have created at most this many assignments. "
        f"Teachers who have created none count as 0. {_SUPER_ADMIN_ONLY}"
    ),
    "plan_tier": (
        "Keeps users whose current plan has this tier. The current plan is, "
        "in this order: a school license seat as a teacher, a school license "
        "they run as its admin, or their own active subscription. Users with "
        f"no plan are left out. {_SUPER_ADMIN_ONLY}"
    ),
    "plan_category": (
        "Keeps users whose current plan is of this kind: Individual (their "
        "own subscription) or License (a school license). Users with no plan "
        f"are left out. {_SUPER_ADMIN_ONLY}"
    ),
    "plan_interval": (
        "Keeps users whose current plan bills this often. Users with no plan "
        f"are left out. {_SUPER_ADMIN_ONLY}"
    ),
    "has_plan": (
        "true: keeps users who have a current plan (a school license seat, a "
        "school license they run, or their own active subscription). false: "
        f"keeps users who have none. {_EMPTY} {_SUPER_ADMIN_ONLY}"
    ),
    "signals_min": (
        "Keeps users who meet at least this many of the 4 signals "
        "(at_80_percent, frequent_user, active_last_week, is_power_grader), "
        "from 0 to 4. Only teachers can have signals, so 1 or more keeps "
        f"teachers only, and 0 keeps everyone. {_EMPTY} {_SUPER_ADMIN_ONLY}"
    ),
    "at_80_percent": (
        "true: keeps teachers who have been marked as reaching 80% of their "
        "beta credits. Once marked, it stays marked. false: keeps everyone "
        f"else. {_TEACHERS_ONLY} {_EMPTY} {_SUPER_ADMIN_ONLY}"
    ),
    "frequent_user": (
        "true: keeps teachers who have been active on 8 or more different "
        f"days. false: keeps everyone else. {_TEACHERS_ONLY} {_EMPTY} "
        f"{_SUPER_ADMIN_ONLY}"
    ),
    "active_last_week": (
        "true: keeps teachers who were last active in the past 7 days, "
        "counting back from the moment you ask, to the second; exactly 7 "
        "days ago still counts. A teacher who was never active counts as "
        f"false. false: keeps everyone else. {_TEACHERS_ONLY} {_EMPTY} "
        f"{_SUPER_ADMIN_ONLY}"
    ),
    "is_power_grader": (
        "true: keeps teachers who have spent more credits on grading than on "
        "creating assignments (reading them in and generating them). Equal "
        "amounts count as false, and credits spent on feedback are not "
        f"counted. false: keeps everyone else. {_TEACHERS_ONLY} {_EMPTY} "
        f"{_SUPER_ADMIN_ONLY}"
    ),
}
for _name, _text in FILTER_HELP.items():
    UserEnrollmentFilter.base_filters[_name].extra["help_text"] = _text


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
