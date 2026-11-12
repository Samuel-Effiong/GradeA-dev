"""The sales-leads list's plan, last-login, activated and usage filters, and
its usage sorts (beta bug fixes, owner 2026-11-12).

GET analytics beta/intent-signals takes the same last_login_*, activated_*,
plan_tier, plan_category, plan_interval, has_plan and the eight usage ranges
(credits_used, credits_left, papers_graded, assignments_created, _min and
_max) as GET /api/v1/users/, and they mean the same. `ordering` also takes
the user list's credits_used, credits_left, papers_graded,
assignments_created and activated_at sorts (apply_sort). Nothing is copied: the matching
teachers come from the user list's own code (users.user_list.ensure, the way
users.filters.UserEnrollmentFilter.filter_list_value uses it), and the leads
are the BetaProfiles of those users.

No super admin check here: the endpoint is super admin only already.
"""

import django_filters
from django.db.models import F, OuterRef, QuerySet, Subquery

from users import user_list
from users.filters import (  # noqa: PLC2701 - the sentence this drops
    _SUPER_ADMIN_ONLY,
    FILTER_HELP,
    UserEnrollmentFilter,
)
from users.models import CustomUser

#: The user list's own declarations (users.filters), so a value is accepted
#: or refused exactly as there.
NAMES = ("last_login_after", "last_login_before", "activated_after",
         "activated_before", "plan_tier", "plan_category", "plan_interval",
         "has_plan", "credits_used_min", "credits_used_max", "credits_left_min",
         "credits_left_max", "papers_graded_min", "papers_graded_max",
         "assignments_created_min", "assignments_created_max")  # fmt: skip


def _declared(name):
    """The user list's filter for `name`, pointed at this FilterSet's method."""
    declared = UserEnrollmentFilter.base_filters[name]
    return type(declared)(
        method="filter_list_value",
        **(
            {"choices": declared.extra["choices"]}
            if "choices" in declared.extra
            else {}
        ),
    )


class LeadFilterSet(django_filters.FilterSet):
    last_login_after = _declared("last_login_after")
    last_login_before = _declared("last_login_before")
    plan_tier = _declared("plan_tier")
    plan_category = _declared("plan_category")
    plan_interval = _declared("plan_interval")
    has_plan = _declared("has_plan")
    activated_after = _declared("activated_after")
    activated_before = _declared("activated_before")
    credits_used_min = _declared("credits_used_min")
    credits_used_max = _declared("credits_used_max")
    credits_left_min = _declared("credits_left_min")
    credits_left_max = _declared("credits_left_max")
    papers_graded_min = _declared("papers_graded_min")
    papers_graded_max = _declared("papers_graded_max")
    assignments_created_min = _declared("assignments_created_min")
    assignments_created_max = _declared("assignments_created_max")

    def filter_list_value(self, queryset: QuerySet, name, value):
        users = CustomUser.objects.all()
        if name == "has_plan":
            users = user_list.ensure(users, "current_plan_id").filter(
                current_plan_id__isnull=not value
            )
        else:
            field, lookup = UserEnrollmentFilter.LIST_FILTERS[name]
            if field != "last_login":
                users = user_list.ensure(users, field)
            users = users.filter(**{f"{field}__{lookup}": value})
        return queryset.filter(user__in=users.values("pk"))


def help_text(name):
    """The owner-approved Swagger text of `name` (users.filters.FILTER_HELP,
    brief 6), less its super-admin sentence: this endpoint is super admin only
    already."""
    return FILTER_HELP[name].replace(f" {_SUPER_ADMIN_ONLY}", "")


#: The sorts taken from the user list (users.user_list.SORT_KEYS), each with
#: or without "-".
SORT_KEYS = ("credits_used", "credits_left", "papers_graded", "assignments_created",
             "activated_at")  # fmt: skip
SORT_VALUES = tuple(v for key in SORT_KEYS for v in (key, f"-{key}"))


def apply_sort(queryset, ordering):
    """Order BetaProfiles by the teacher's user list value: the same raw
    value the user list sorts on, empty ones last both ways, then user id."""
    descending = ordering.startswith("-")
    target = user_list.SORT_KEYS[ordering.lstrip("-")]
    value = Subquery(
        user_list.ensure(
            CustomUser.objects.filter(pk=OuterRef("user_id")), target, annotate=True
        )
        .order_by()
        .values(target)[:1]
    )
    queryset = queryset.annotate(lead_sort_value=value)
    expression = F("lead_sort_value")
    return queryset.order_by(
        (
            expression.desc(nulls_last=True)
            if descending
            else expression.asc(nulls_last=True)
        ),
        "user_id",
    )


#: The values a lead row shows besides its own: the user list's (LISTED).
ROW_VALUES = ("plan_tier", "plan_category", "plan_interval", "credits_used",
              "credits_left", "papers_graded", "assignments_created",
              "activated_at")  # fmt: skip


def row_values(user_ids):
    """user id -> {value: ...} for one page of leads, in ONE query, from the
    user list's own expressions (users.user_list), so the cost does not grow
    with the page."""
    users = user_list.ensure(
        CustomUser.objects.filter(pk__in=list(user_ids)), *ROW_VALUES, annotate=True
    )
    return {row["pk"]: row for row in users.values("pk", *ROW_VALUES)}
