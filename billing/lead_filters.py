"""The sales-leads list's plan and last-login filters (beta bug fix, owner
2026-11-12).

GET analytics beta/intent-signals takes the same last_login_after,
last_login_before, plan_tier, plan_category, plan_interval and has_plan as
GET /api/v1/users/, and they mean the same. Nothing is copied: the matching
teachers come from the user list's own code (users.user_list.ensure, the way
users.filters.UserEnrollmentFilter.filter_list_value uses it), and the leads
are the BetaProfiles of those users.

No super admin check here: the endpoint is super admin only already.
"""

import django_filters
from django.db.models import QuerySet

from users import user_list
from users.filters import (  # noqa: PLC2701 - the sentence this drops
    _SUPER_ADMIN_ONLY,
    FILTER_HELP,
    UserEnrollmentFilter,
)
from users.models import CustomUser

#: The user list's own declarations (users.filters), so a value is accepted
#: or refused exactly as there.
NAMES = ("last_login_after", "last_login_before", "plan_tier", "plan_category",
         "plan_interval", "has_plan")  # fmt: skip


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
