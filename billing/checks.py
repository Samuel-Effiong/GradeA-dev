"""
Read-only Django system check that surfaces (without ever writing to the
database) whether the PlanFeature catalogue rows that
billing/access_control.py's tier gate depends on have actually been seeded
in this environment via `manage.py seed_plan_features`.

Deliberately a WARNING, not an ERROR, and deliberately read-only: an
earlier version of this fix auto-seeded the catalogue from a post_migrate
signal, but that directly defeated
billing/tests/test_access_control.py::test_missing_plan_feature_row_denies_by_default,
which intentionally asserts that an unseeded gate denies access by
default (see _plan_includes_gating_feature's docstring - "deny by
default" is the intended safe behavior for a genuinely unconfigured
gate, not a bug to paper over). Surfacing the gap loudly on every
`manage.py check` (and therefore on `runserver`/`migrate`/deploy
pipelines that run checks) is the correct fix: it keeps a human in the
loop instead of the AI feature tier gate silently running in either an
unintentionally-locked-out or an unintentionally-wide-open state.
"""

from django.core.checks import Error, Warning, register


@register("billing")
def check_atomic_requests_disabled(app_configs, **kwargs):
    """
    ATOMIC_REQUESTS must stay OFF for the Stripe webhook idempotency claim
    to work at all.

    billing/webhooks.py claims an event with a conditional UPDATE that must
    COMMIT before the handler runs, so that a concurrent redelivery of the
    same event can see the claim and back off with 409. Under
    ATOMIC_REQUESTS the whole view runs in one transaction, so the claim
    stays invisible until the response is returned — at which point the
    racing delivery has already been told "duplicate, 200" for work that
    had not finished. That is exactly the permanent event-loss bug the
    status column was introduced to kill.

    This is an ERROR rather than a comment because the failure mode is
    invisible: every test would still pass (a Django TestCase wraps each
    test in a transaction regardless), and the loss would only show up as
    customers who paid and received nothing. `manage.py check` runs in
    deploy pipelines, so this fails loudly instead of rotting.
    """
    from django.conf import settings

    offenders = [
        alias
        for alias, config in settings.DATABASES.items()
        if config.get("ATOMIC_REQUESTS")
    ]
    if not offenders:
        return []

    return [
        Error(
            f"ATOMIC_REQUESTS is enabled on database alias(es) {sorted(offenders)}. "
            f"This silently breaks the Stripe webhook idempotency claim in "
            f"billing/webhooks.py: the claim would no longer commit before the "
            f"handler runs, so a concurrent redelivery cannot see it and gets "
            f"told 'duplicate' for work that has not finished - the exact "
            f"event-loss bug the StripeEvent.status column exists to prevent. "
            f"Turn it off, or rework _claim_stripe_event to use its own "
            f"connection first.",
            id="billing.E001",
        )
    ]


@register("billing")
def check_plan_feature_catalogue_seeded(app_configs, **kwargs):
    from django.db import DatabaseError
    from django.db.utils import ProgrammingError

    from billing.access_control import AI_FEATURE_GATING_MAP
    from billing.models import PlanFeature

    try:
        seeded_gating_keys = set(
            PlanFeature.objects.filter(is_gating_feature=True).values_list(
                "key", flat=True
            )
        )
    except (DatabaseError, ProgrammingError):
        # Tables don't exist yet (e.g. before the very first `migrate`) -
        # `migrate` itself is the authority on schema problems, not this
        # check.
        return []

    required_keys = {str(key) for key in AI_FEATURE_GATING_MAP.values()}
    missing = required_keys - {str(key) for key in seeded_gating_keys}

    if not missing:
        return []

    return [
        Warning(
            f"PlanFeature catalogue is missing (or not marked "
            f"is_gating_feature=True for) gating key(s): {sorted(missing)}. "
            f"Every user in this environment will be DENIED the "
            f"corresponding AI feature(s) - see AI_FEATURE_GATING_MAP in "
            f"billing/access_control.py - until "
            f"`manage.py seed_plan_features` is run.",
            id="billing.W001",
        )
    ]
