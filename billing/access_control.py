"""
Access control helpers for checking if a user can use AI features.

These helpers enforce the rule:
- User can use AI ONLY if they have an active subscription/allocation with
  remaining credits.
- Trial users (individual track only) must have is_active=True AND
  remaining credits > 0, AND must be within their trial window.
- Expired or out-of-credits users cannot use AI.

Tier-based feature access (see the Grade A+ Subscription Model):
- INDIVIDUAL track: gated by UserSubscription.plan (STANDARD/PRO/POWER/TRIAL).
- LICENSE (institutional) track has two distinct sub-cases:
    - Teachers are gated by the license's plan (PRO_LICENSE/POWER_LICENSE/
      CUSTOM_*) via their SchoolCreditAllocation.
    - The school admin is gated by a fixed, tier-independent allowlist of
      analytics-only features instead (ADMIN_ALLOWED_AI_FEATURES) — admins
      don't occupy a plan tier of their own; their dashboard just needs
      *some* AI credits to generate analytics, regardless of which plan
      the license happens to be on.
- Specific premium AI features (e.g. AI Prompt-Based Assignment Creation)
  are additionally gated per-plan via the PlanFeature / PlanFeatureInclusion
  models — see AI_FEATURE_GATING_MAP. Only features whose PlanFeature row
  has is_gating_feature=True are ever enforced; anything not in the map,
  or mapped to a non-gating (display-only) PlanFeature, is treated as a
  baseline feature available to any user who otherwise passes the checks
  above (matches "Batch Grading/Uploading" style features being available
  starting at the lowest paid tier).
- Student-initiated AI calls (a student's submission triggering grading)
  are billed against — and therefore gated by — the ASSIGNMENT'S TEACHER,
  never the student's own account (students never have a subscription or
  wallet of their own). See can_ai_be_used_for_assignment().

To be used in:
- Views
- Task processing to reject work
- AIProcessor.execute_graded_task (the actual credit-consuming chokepoint)
"""

import logging
from dataclasses import dataclass
from functools import wraps
from typing import Any, Optional, Tuple

from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response

from .models import PlanFeature, PlanFeatureKey

# from billing.models import CreditWallet  #, UserSubscription

logger = logging.getLogger(__name__)


class AIFeatureNotAvailableError(Exception):
    """
    Raised when a user (or, for student submissions, the assignment's
    teacher) is authenticated, active, and has credits, but their current
    plan/allocation does not include the specific AI feature being
    requested. Distinct from InsufficientCreditsError — this is a plan/tier
    permission problem ("upgrade to unlock this"), not a balance problem
    ("buy more credits").
    """

    pass


# The two can_user_access_ai() reasons that indicate a credit-BALANCE
# problem rather than a plan/tier permission problem. Callers (see
# AIProcessor.execute_graded_task) match against these to decide whether
# to raise InsufficientCreditsError (balance) instead of
# AIFeatureNotAvailableError (permission) — kept as named constants here,
# next to where they're produced, rather than duplicated as string
# literals at each call site.
NO_CREDITS_REMAINING_REASON = (
    "No credits remaining. Please purchase more credits to continue using AI features."
)
TRIAL_CREDITS_EXHAUSTED_REASON = (
    "Out of trial credits. Please subscribe to continue using AI features."
)


# Maps an AI-processor `feature` string (the exact value passed as
# `feature=` into AIProcessor.execute_graded_task) to the PlanFeatureKey
# that must be actively included AND marked as a code-enforced gate
# (PlanFeature.is_gating_feature=True) on the user's current plan for them
# to use it.
#
# Deliberately NOT exhaustive — baseline features available starting at
# the lowest paid tier ("Grading Assignment", "Assignment Extraction",
# "Answer Extraction", "Formatted Grade", "Student Summary" — the last of
# which corresponds to the image's "AI Student Summaries and Suggested
# Interventions", explicitly listed as a STANDARD-tier bullet) are
# intentionally left unmapped, so they fall through to "no gating
# required" and stay available to every active, credit-having user.
#
# NOTE: "Weekly Course Summary" is mapped here as a best-effort inference
# (it's the closest AI-processor call to the image's "AI Prompt-Based
# Analytics & Insights" Pro+ bullet) — there is no AI-processor call site
# in this codebase corresponding to "Pre-Scheduled Grading" (a scheduling
# feature, not a distinct AI generation call) or the image's
# Power-exclusive "Weekly E-mail Summaries" specifically, so those aren't
# wired up here. Review/adjust this mapping against your actual
# PlanFeatureInclusion seed data.
AI_FEATURE_GATING_MAP = {
    "Assignment Generation": PlanFeatureKey.AI_PROMPT_ASSIGNMENT_CREATION,
    "Weekly Course Summary": PlanFeatureKey.AI_PROMPT_ANALYTICS_SUMMARY,
}

# AI-processor `feature` strings a school admin's fixed analytics
# allocation is allowed to use. Deliberately NOT tier-gated (there is no
# "admin tier" to check against a license plan) and deliberately a small
# allowlist rather than the full teacher feature set, since admins cannot
# grade, extract assignments, or otherwise act on courses themselves — see
# LicenseSubscriptionService.ADMIN_ANALYTICS_CREDITS_RAW / the top-left
# note on the subscription model diagram ("Teachers... Students never
# pay"; admins manage billing, not grading).
#
# Every AI-processor call site whose caller passes a SCHOOL_ADMIN user
# (dashboard/views.py's schooladmin custom-AI-prompt endpoint included)
# must have its `feature=` string listed here, or it is unconditionally
# blocked for every school admin regardless of plan/credits — this bit
# the schooladmin custom-AI-prompt dashboard feature before
# "Schooladmin Custom AI Prompt" was added below.
ADMIN_ALLOWED_AI_FEATURES = frozenset(
    {
        "Weekly Course Summary",
        "Schooladmin Custom AI Prompt",
        "Weekly School Admin Summary",
    }
)


@dataclass
class AccessContext:
    """
    Normalized view of "what governs this user's AI access right now",
    resolved once by _resolve_access_context() and reused by every check
    below, so the credit check and the feature-tier check can never
    disagree about which subscription/allocation is authoritative for a
    given user.

    kind: one of "individual", "license_teacher", "license_admin", "none".
    """

    kind: str
    plan: Optional[Any] = None
    is_trial: bool = False
    trial_end: Optional[Any] = None
    wallet: Optional[Any] = None


def _get_wallet(user):
    """
    Safely fetches user.credit_wallet. A reverse OneToOneField with no
    matching row raises CreditWallet.DoesNotExist (NOT AttributeError), so
    this must use try/except rather than getattr(..., default) — getattr's
    default only suppresses AttributeError, not arbitrary ORM exceptions.
    """
    try:
        return user.credit_wallet
    except Exception:
        return None


def _resolve_access_context(user) -> AccessContext:
    """
    Determines which subscription/allocation governs `user`'s AI access,
    across BOTH the individual and license tracks — including the
    school-admin's own fixed analytics allocation, which previously fell
    through every branch of CustomUser.get_active_subscription() (that
    method only checks the license path for is_teacher()==True, and
    SCHOOL_ADMIN users have no individual UserSubscription either, so
    admins resolved to "no subscription" everywhere before this).

    Precedence when a user somehow matches more than one case (shouldn't
    happen given the enrollment-time guards elsewhere in this codebase,
    but resolved deterministically regardless): license teacher > license
    admin > individual > none.
    """
    # 1. License track — teacher allocation (non-admin).
    teacher_allocation = (
        user.school_credit_allocations.filter(
            is_active=True,
            is_admin_allocation=False,
            license_subscription__is_active=True,
        )
        .select_related("license_subscription__plan")
        .first()
    )
    if teacher_allocation:
        return AccessContext(
            kind="license_teacher",
            plan=teacher_allocation.license_subscription.plan,
            is_trial=False,
            wallet=_get_wallet(user),
        )

    # 2. License track — the admin's own analytics allocation.
    admin_allocation = (
        user.school_credit_allocations.filter(
            is_active=True,
            is_admin_allocation=True,
            license_subscription__is_active=True,
        )
        .select_related("license_subscription__plan")
        .first()
    )
    if admin_allocation:
        return AccessContext(
            kind="license_admin",
            plan=admin_allocation.license_subscription.plan,
            is_trial=False,
            wallet=_get_wallet(user),
        )

    # 3. Individual track.
    individual_sub = (
        user.subscriptions.filter(is_active=True).select_related("plan").first()
    )
    if individual_sub:
        return AccessContext(
            kind="individual",
            plan=individual_sub.plan,
            is_trial=bool(individual_sub.is_trial),
            trial_end=individual_sub.trial_end,
            wallet=_get_wallet(user),
        )

    # 4. Nothing found on either track.
    return AccessContext(kind="none", wallet=_get_wallet(user))


def _plan_includes_gating_feature(plan, feature_key: str) -> bool:
    """
    True only if `plan` actively includes `feature_key` AND that
    PlanFeature is actually marked as a code-enforced gate
    (is_gating_feature=True). A PlanFeature that exists purely as a
    display-only catalogue label (is_gating_feature=False) never blocks
    access on its own, regardless of `included`. Absence of the
    PlanFeature/PlanFeatureInclusion row entirely also resolves to False
    (deny by default) rather than silently allowing everyone through an
    unconfigured gate.
    """
    if plan is None:
        return False

    feature = PlanFeature.objects.filter(pk=feature_key).first()
    if feature is None:
        # No catalogue row at all - misconfiguration, deny by default.
        return False

    if not feature.is_gating_feature:
        # Display-only catalogue label - never a real gate, regardless of
        # whether any plan's PlanFeatureInclusion.included is True/False.
        return True

    return plan.feature_inclusions.filter(feature=feature, included=True).exists()


def can_user_access_ai(
    user, feature: Optional[str] = None
) -> Tuple[bool, Optional[str]]:
    """
    Check if a user can access AI features, optionally scoped to one
    specific AI feature.

    Rules:
    1. User must be authenticated and active.
    2. User must have an active AI-credit-bearing context — an individual
       UserSubscription (trial or paid), a license teacher allocation, or
       a license admin's own analytics allocation. (Previously this only
       ever checked user.subscriptions, so every license-enrolled teacher
       AND the license admin always resolved to "No active subscription"
       here, regardless of their actual license state — see
       _resolve_access_context.)
    3. If on an individual trial: is_active=True AND within the trial
       window AND remaining_credits > 0.
    4. Otherwise: remaining_credits > 0.
    5. If `feature` is given, additionally gate on tier/role:
       a. School admins (kind="license_admin") may only use features
          listed in ADMIN_ALLOWED_AI_FEATURES — regardless of the
          license's plan tier, since there is no "admin tier".
       b. Everyone else: if `feature` is in AI_FEATURE_GATING_MAP, the
          user's resolved plan must actively include that gating
          PlanFeature (see _plan_includes_gating_feature). Features not
          in the map are baseline and always permitted once 1-4 pass.

    Args:
        user: CustomUser instance (or AnonymousUser)
        feature: Optional AI-processor feature string to additionally
            gate (e.g. "Assignment Generation"). None = base access only,
            no feature-tier check performed.

    Returns:
        (bool, Optional[str]): (can_access, reason_if_blocked)
        Examples:
        - (True, None) → User can access
        - (False, "Trial expired") → User blocked, with reason
        - (False, "No active subscription") → User blocked
    """

    # Check 1: User is authenticated
    if not user or not user.is_authenticated:
        return False, "User not authenticated"

    if not user.is_active:
        return False, "User account is inactive"

    # Check 2: User has SOME active AI-credit-bearing context
    try:
        context = _resolve_access_context(user)
    except Exception as exc:
        logger.error(
            "Error resolving AI access context for user %s: %s",
            user.email,
            str(exc),
        )
        return False, "Internal Error: Could not verify subscription status"

    if context.kind == "none":
        return False, "No active subscription"

    # Check 3: For individual-track TRIAL subscriptions, verify trial_end
    # hasn't passed. (License allocations are never trials — is_trial is
    # hardcoded False for both license_teacher and license_admin in
    # _resolve_access_context.)
    if context.kind == "individual" and context.is_trial:
        now = timezone.now()

        # Guard: trial_end should always be set for trials
        if not context.trial_end:
            logger.warning(
                "Trial subscription has no trial_end set. User %s.",
                user.email,
            )
            return False, "Internal Error: Trial subscription configuration issue"

        # Check if trial window has passed
        if context.trial_end <= now:
            return (
                False,
                "Trial period has expired. Please subscribe to continue using AI features.",
            )

    # Check 4: User must have credits remaining (for every context kind)
    if context.wallet is None:
        logger.error(
            "Error fetching wallet for user %s: no CreditWallet found",
            user.email,
        )
        return False, "Internal Error: Could not verify credit balance"

    remaining_credits = context.wallet.total_remaining_credits()

    if remaining_credits <= 0:
        if context.kind == "individual" and context.is_trial:
            return (
                False,
                TRIAL_CREDITS_EXHAUSTED_REASON,
            )
        else:
            return (
                False,
                NO_CREDITS_REMAINING_REASON,
            )

    # Check 5: Feature-level tier/role gating, only when a specific
    # feature was requested.
    if feature:
        if context.kind == "license_admin":
            if feature not in ADMIN_ALLOWED_AI_FEATURES:
                return (
                    False,
                    "This AI feature is not available to school admin accounts.",
                )
        else:
            required_key = AI_FEATURE_GATING_MAP.get(feature)
            if required_key and not _plan_includes_gating_feature(
                context.plan, str(required_key)
            ):
                return (
                    False,
                    "Your current plan does not include this feature. "
                    "Please upgrade to access it.",
                )

    # All checks passed

    return True, None


def can_ai_be_used_for_assignment(
    assignment, feature: Optional[str] = None
) -> Tuple[bool, Optional[str]]:
    """
    Resolves the responsible teacher for `assignment` and checks THEIR AI
    access. Use this for student-submission-triggered AI calls, where the
    student's own account has no subscription/wallet of its own —
    consumption is billed against assignment.course.teacher instead (see
    AIProcessor.execute_graded_task's STUDENT branch), so it's the
    teacher's tier/credits that must be checked, never the student's.

    Args:
        assignment: The Assignment the student is submitting to. Must have
            a resolvable .course.teacher.
        feature: Optional AI-processor feature string to additionally
            gate — see can_user_access_ai.

    Returns:
        (bool, Optional[str]): (can_access, reason_if_blocked)
    """
    if assignment is None:
        return (
            False,
            "Assignment is required to resolve AI access for a student submission",
        )

    try:
        teacher = assignment.course.teacher
    except Exception as exc:
        logger.error(
            "Could not resolve teacher for assignment %s: %s",
            getattr(assignment, "id", "unknown"),
            str(exc),
        )
        return False, "Internal Error: Could not resolve the assignment's teacher"

    return can_user_access_ai(teacher, feature=feature)


def get_user_ai_access_status(user, feature: Optional[str] = None) -> dict:
    """
    Get detailed status of user's AI access, across both the individual
    and license tracks.

    Returns a dict with:
    - can_access (bool)
    - reason (str or None)
    - subscription_type (str: "TRIAL" / "PAID" / "LICENSE_TEACHER" /
      "LICENSE_ADMIN" / None)
    - days_remaining_in_trial (int or None)
    - credits_remaining (int)
    - credits_remaining_display (int)
    - is_trial (bool)
    - is_active (bool)

    Use this for building frontend status displays.

    Args:
        user: CustomUser instance
        feature: Optional AI-processor feature string — if given,
            can_access/reason reflect that specific feature's tier gate
            too (see can_user_access_ai).

    Returns:
        dict with access status and details
    """

    can_access, reason = can_user_access_ai(user, feature=feature)

    if not user or not user.is_authenticated:
        return {
            "can_access": can_access,
            "reason": reason,
            "subscription_type": None,
            "days_remaining_in_trial": None,
            "credits_remaining": 0,
            "credits_remaining_display": 0,
            "is_trial": False,
            "is_active": False,
        }

    now = timezone.now()

    try:
        context = _resolve_access_context(user)
    except Exception as exc:
        logger.error(
            "Error resolving AI access context for user %s: %s",
            user.email,
            str(exc),
        )
        return {
            "can_access": can_access,
            "reason": reason,
            "subscription_type": None,
            "days_remaining_in_trial": None,
            "credits_remaining": 0,
            "credits_remaining_display": 0,
            "is_trial": False,
            "is_active": False,
        }

    # Fetch wallet balance
    remaining_credits = 0
    remaining_credits_display = 0

    if context.wallet is not None:
        try:
            remaining_credits = context.wallet.total_remaining_credits()
            remaining_credits_display = context.wallet.display_balance
        except Exception as exc:
            logger.error(
                "Error computing wallet balance for user %s: %s",
                user.email,
                str(exc),
            )

    # Derive subscription_type / trial countdown per context kind
    days_remaining_in_trial = None
    subscription_type = None
    is_trial = False
    is_active = context.kind != "none"

    if context.kind == "individual":
        is_trial = context.is_trial
        if context.is_trial:
            subscription_type = "TRIAL"
            if context.trial_end:
                delta = context.trial_end - now
                days_remaining_in_trial = max(0, delta.days)
        else:
            subscription_type = "PAID"
    elif context.kind == "license_teacher":
        subscription_type = "LICENSE_TEACHER"
    elif context.kind == "license_admin":
        subscription_type = "LICENSE_ADMIN"

    data = {
        "can_access": can_access,
        "reason": reason,
        "subscription_type": subscription_type,
        "days_remaining_in_trial": days_remaining_in_trial,
        "credits_remaining": remaining_credits,
        "credits_remaining_display": remaining_credits_display,
        "is_trial": is_trial,
        "is_active": is_active,
    }

    return data


def require_ai_access(view_func=None, *, feature: Optional[str] = None):
    """
    Decorator for DRF views to enforce AI access control, optionally
    scoped to a specific AI feature's tier gate.

    Usage in views:
        @require_ai_access
        @action(detail=False, methods=['post'])
        def grade_assignment(self, request):
            ...

        @require_ai_access(feature="Assignment Generation")
        @action(detail=False, methods=['post'])
        def generate_assignment(self, request):
            ...

    Or in middleware to protect entire endpoints.

    Args:
        view_func: The view function being wrapped (bound automatically
            when used as a bare `@require_ai_access`; None when called as
            `@require_ai_access(...)`, in which case the inner decorator
            is returned instead — standard optional-argument-decorator
            pattern).
        feature: Optional AI-processor feature string to additionally
            gate — see can_user_access_ai. Keyword-only so it never
            collides with the bare-decorator call form above.

    Returns:
        403 Forbidden if user cannot access AI (or this specific feature)
        200 OK if user can access (view executes normally)
    """

    def decorator(func):
        @wraps(func)
        def wrapper(self, request, *args, **kwargs):
            can_access, reason = can_user_access_ai(request.user, feature=feature)

            if not can_access:
                logger.warning(
                    "AI access denied for user %s (feature=%s): %s",
                    request.user.email if request.user.is_authenticated else "ANON",
                    feature,
                    reason,
                )

                return Response(
                    {
                        "detail": f"AI access denied: {reason}",
                        "reason_code": (
                            reason.lower().replace(" ", "_") if reason else "unknown"
                        ),
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )

            return func(self, request, *args, **kwargs)

        return wrapper

    # Allow use with or without parentheses
    if view_func is None:
        return decorator
    return decorator(view_func)


def get_remaining_trial_days(user) -> int:
    """
    Get the number of whole days remaining in user's trial.

    Returns 0 if:
    - User has no trial
    - Trial has ended
    - User is on a paid plan

    Args:
        user: CustomUser instance

    Returns:
        int: Days remaining (0 if no trial or expired)
    """
    if not user or not user.is_authenticated:
        return 0

    try:
        active_sub = user.subscriptions.filter(is_active=True, is_trial=True).first()
        if not active_sub or not active_sub.trial_end:
            return 0

        delta = active_sub.trial_end - timezone.now()
        return max(0, delta.days)
    except Exception as exc:
        logger.error(
            "Error fetching trial days for user %s: %s",
            user.email,
            str(exc),
        )
        return 0


def is_user_on_active_trial(user) -> bool:
    """
    Quick check: is user currently on an active trial?

    Args:
        user: CustomUser instance

    Returns:
        bool: True if user has active is_trial=True subscription
    """

    if not user or not user.is_authenticated:
        return False

    try:
        return user.subscriptions.filter(is_active=True, is_trial=True).exists()
    except Exception:
        return False


def is_user_trial_expired(user) -> bool:
    """
    Quick check: has user's trial expired?

    This does NOT check credits — only the time window.

    Args:
        user: CustomUser instance

    Returns:
        bool: True if user was on trial and trial_end has passed
    """

    if not user or not user.is_authenticated:
        return False

    try:
        active_sub = user.subscriptions.filter(is_active=True, is_trial=True).first()
        if not active_sub or not active_sub.trial_end:
            return False

        return active_sub.trial_end <= timezone.now()
    except Exception:
        return False


# ============================================================================
# INTEGRATION EXAMPLES
# ============================================================================
#
# 1. In a view:
#
#    from billing.access_control import require_ai_access
#
#    @require_ai_access
#    @action(detail=False, methods=['post'])
#    def grade_assignment(self, request):
#        # User is guaranteed to have access here
#        ...
#
# 2. In serializer:
#
#    def to_representation(self, instance):
#        ret = super().to_representation(instance)
#        can_access, _ = can_user_access_ai(self.context['request'].user)
#        ret['ai_access_enabled'] = can_access
#        return ret
#
# 3. In middleware:
#
#    class AIAccessMiddleware:
#        def __init__(self, get_response):
#            self.get_response = get_response
#
#        def __call__(self, request):
#            # Check before processing
#            if request.path.startswith('/api/ai/'):
#                can_access, reason = can_user_access_ai(request.user)
#                if not can_access:
#                    return Response({'detail': reason}, status=403)
#            return self.get_response(request)
#
# 4. In Celery task:
#
#    @shared_task
#    def grade_assignment_task(user_id, assignment_id):
#        user = CustomUser.objects.get(id=user_id)
#        can_access, reason = can_user_access_ai(user)
#        if not can_access:
#            logger.info("Task rejected: %s", reason)
#            return {"error": reason}
#        # Process grading...
#
