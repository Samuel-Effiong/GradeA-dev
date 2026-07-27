from dataclasses import dataclass
from typing import Optional

from .models import LicenseSubscription, SchoolCreditAllocation, UserSubscription

SOURCE_INDIVIDUAL = "INDIVIDUAL"
SOURCE_LICENSE_TEACHER = "LICENSE_TEACHER"
SOURCE_LICENSE_ADMIN = "LICENSE_ADMIN"


@dataclass
class UserBillingContext:
    source: Optional[str]
    user_subscription: Optional[UserSubscription] = None
    allocation: Optional[SchoolCreditAllocation] = None
    license_subscription: Optional[LicenseSubscription] = None
    managed_license_count: int = 0


def resolve_user_billing_context(user) -> UserBillingContext:
    """
    Resolution order (first match wins — deliberately deterministic so a
    user who somehow matches more than one path never gets a flaky
    result depending on query timing):

      1. Active UserSubscription (INDIVIDUAL) — a directly-paid-for
         subscription always takes priority over any license role.
      2. Active LicenseSubscription where user == admin_user
         (LICENSE_ADMIN).
      3. Active, non-admin SchoolCreditAllocation under an active
         LicenseSubscription (LICENSE_TEACHER). is_admin_allocation=True
         rows are excluded here on purpose — the admin's OWN analytics
         allocation must never be mistaken for a teacher enrollment.
      4. None of the above -> source=None; caller should 404.

    If a user administers more than one active license (the School<->
    admin relationship is many-to-many, so this is possible), the most
    recently created one is returned, and managed_license_count reflects
    the true total so the caller can surface that honestly rather than
    silently hiding the ambiguity.
    """
    user_sub = (
        UserSubscription.objects.filter(user=user, is_active=True)
        .select_related("plan", "pending_plan")
        .first()
    )
    if user_sub:
        return UserBillingContext(source=SOURCE_INDIVIDUAL, user_subscription=user_sub)

    managed_licenses = (
        LicenseSubscription.objects.filter(admin_user=user, is_active=True)
        .select_related("plan", "school")
        .order_by("-created_at")
    )
    managed_count = managed_licenses.count()
    if managed_count > 0:
        return UserBillingContext(
            source=SOURCE_LICENSE_ADMIN,
            license_subscription=managed_licenses.first(),
            managed_license_count=managed_count,
        )

    allocation = (
        SchoolCreditAllocation.objects.filter(
            user=user,
            is_active=True,
            is_admin_allocation=False,
            license_subscription__is_active=True,
        )
        .select_related(
            "license_subscription",
            "license_subscription__plan",
            "license_subscription__school",
            "license_subscription__admin_user",
        )
        .order_by("-created_at")
        .first()
    )
    if allocation:
        return UserBillingContext(
            source=SOURCE_LICENSE_TEACHER,
            allocation=allocation,
            license_subscription=allocation.license_subscription,
        )

    return UserBillingContext(source=None)


def get_monthly_credit_ceiling_for_user(user) -> int:
    """
    Raw monthly credit ceiling used as the wallet progress-bar "100%"
    baseline, resolved across BOTH tracks.

    For a license TEACHER, deliberately uses the allocation's OWN
    monthly_allocation rather than the plan's nominal monthly_credits —
    monthly_allocation is the teacher's real per-cycle grant and can be
    LOWER than the plan default when capped by the license's seat/global
    budget (see LicenseSubscriptionService._enroll_teacher_internal).
    Using the nominal plan value there would show a progress bar that
    never reaches 100% even when the teacher's actual grant is fully
    consumed.

    For a license ADMIN, uses their own fixed analytics allocation
    (independent of the plan's teacher-facing monthly_credits).
    """
    context = resolve_user_billing_context(user)

    if context.source == SOURCE_INDIVIDUAL:
        return context.user_subscription.plan.monthly_credits or 0

    if context.source == SOURCE_LICENSE_TEACHER:
        return context.allocation.monthly_allocation or 0

    if context.source == SOURCE_LICENSE_ADMIN:
        admin_allocation = SchoolCreditAllocation.objects.filter(
            license_subscription=context.license_subscription,
            user=user,
            is_admin_allocation=True,
            is_active=True,
        ).first()
        return admin_allocation.monthly_allocation if admin_allocation else 0

    return 0
