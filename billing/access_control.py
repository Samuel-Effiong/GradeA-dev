"""
Access control helpers for checking if a user can use AI features.

These helpers enforce the rule:
- User can use AI ONLY if they have an active subscription with remaining credits
- Trial users must have is_active=True AND remaining credits > 0
- Expired or out-of-credits users cannot use AI


To be used in:
- Views
- Task processing to reject work
"""

import logging
from functools import wraps
from typing import Optional, Tuple

from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response

# from billing.models import CreditWallet  #, UserSubscription

logger = logging.getLogger(__name__)


def can_user_access_ai(user) -> Tuple[bool, Optional[str]]:
    """
    Check if a user can access AI features.

    Rules:
    1. User must be authenticated and active
    2. User must have an active subscription (trial or paid)
    3. If trial: is_active=True AND remaining_credits > 0
    4. If paid: is_active=True AND remaining_credits > 0

    Args:
        user: CustomUser instance (or AnonymousUser)

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

    # Check 2: User has an active subscription
    active_sub = None

    try:
        active_sub = user.subscriptions.filter(is_active=True).first()
    except Exception as exc:
        logger.error(
            "Error fetching subscriptions for user %s: %s",
            user.email,
            str(exc),
        )
        return False, "Internal Error: Could not verify subscription status"

    if not active_sub:
        return False, "No active subscription"

    # Check 3: For TRIAL subscriptions, verify trial_end hasn't passed
    if active_sub.is_trial:
        now = timezone.now()

        # Guard: trial_end should always be set for trials
        if not active_sub.trial_end:
            logger.warning(
                "Trial subscription %s has no trial_end set. User %s.",
                active_sub.id,
                user.email,
            )
            return False, "Internal Error: Trial subscription configuration issue"

        # Check if trial window has passed
        if active_sub.trial_end <= now:
            return (
                False,
                "Trial period has expired. Please subscribe to continue using AI features.",
            )

    # Check 4: User must have credits remaining (for both trial and paid)

    try:
        wallet = user.credit_wallet
    except Exception as exc:
        logger.error(
            "Error fetching wallet for user %s: %s",
            user.email,
            str(exc),
        )
        return False, "Internal Error: Could not verify credit balance"

    remaining_credits = wallet.total_remaining_credits()

    if remaining_credits <= 0:
        if active_sub.is_trial:
            return (
                False,
                "Out of trial credits. Please subscribe to continue using AI features.",
            )
        else:
            return (
                False,
                "No credits remaining. Please purchase more credits to continue using AI features.",
            )

    # All checks passed

    return True, None


def get_user_ai_access_status(user) -> dict:
    """
    Get detailed status of user's AI access.

    Returns a dict with:
    - can_access (bool)
    - reason (str or None)
    - subscription_type (str: "TRIAL" / "PAID" / None)
    - days_remaining_in_trial (int or None)
    - credits_remaining (int)
    - credits_remaining_display (int)

    Use this for building frontend status displays.

    Args:
        user: CustomUser instance

    Returns:
        dict with access status and details
    """

    can_access, reason = can_user_access_ai(user)
    now = timezone.now()

    # Try to fetch subscription info
    active_sub = None
    try:
        active_sub = user.subscription.filter(is_active=True).first()
    except Exception as exc:
        logger.error(
            "Error fetching subscriptions for user %s: %s",
            user.email,
            str(exc),
        )

    # Try to fetch wallet info
    remaining_credits = 0
    remaining_credits_display = 0

    try:
        if user.is_authenticated:
            wallet = user.credit_wallet
            remaining_credits = wallet.total_remaining_credits()
            remaining_credits_display = wallet.display_balance
    except Exception:
        pass

    # Calculate trial days remaining
    days_remaining_in_trial = None
    subscription_type = None

    if active_sub:
        if active_sub.is_trial and active_sub.trial_end:
            subscription_type = "TRIAL"
            delta = active_sub.trial_end - now
            days_remaining_in_trial = max(0, delta.days)
        else:
            subscription_type = "PAID"

    data = {
        "can_access": can_access,
        "reason": reason,
        "subscription_type": subscription_type,
        "days_remaining_in_trial": days_remaining_in_trial,
        "credits_remaining": remaining_credits,
        "credits_remaining_display": remaining_credits_display,
        "is_trial": active_sub.is_trial if active_sub else False,
        "is_active": active_sub.is_active if active_sub else False,
    }

    return data


def require_ai_access(view_func=None):
    """
    Decorator for DRF views to enforce AI access control.

    Usage in views:
        @require_ai_access
        @action(detail=False, methods=['post'])
        def grade_assignment(self, request):
            ...

    Or in middleware to protect entire endpoints.

    Returns:
        403 Forbidden if user cannot access AI
        200 OK if user can access (view executes normally)
    """

    def decorator(func):
        @wraps(func)
        def wrapper(self, request, *args, **kwargs):
            can_access, reason = can_user_access_ai(request.user)

            if not can_access:
                logger.warning(
                    "AI access denied for user %s: %s",
                    request.user.email if request.user.is_authenticated else "ANON",
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
        active_sub = user.subscription.filter(is_active=True, is_trial=True).first()
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
        return user.subscription.filter(is_active=True, is_trial=True).exists()
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
        active_sub = user.subscription.filter(is_active=True, is_trial=True).first()
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
