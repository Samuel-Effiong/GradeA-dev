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

from django.core.checks import Warning, register


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
