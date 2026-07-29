import logging

from django.apps import AppConfig
from django.db.models.signals import post_migrate

logger = logging.getLogger(__name__)


class BillingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "billing"

    def ready(self):
        import billing.signals  # noqa

        post_migrate.connect(_seed_plan_features, sender=self)


def _seed_plan_features(sender, **kwargs):
    """
    Runs the PlanFeature/PlanFeatureInclusion gate seeding after every
    `migrate` of the billing app, so access_control.py's tier gate can
    never silently run against an empty/stale catalogue just because
    `manage.py seed_plan_features` was never run by hand in this
    environment. Idempotent (update_or_create-based) and a no-op for any
    plan that doesn't exist yet, so this is safe to run unconditionally,
    including in test-database setup.
    """
    from billing.management.commands.seed_plan_features import seed_plan_features_data

    try:
        seed_plan_features_data()
    except Exception:
        logger.exception(
            "Failed to auto-seed PlanFeature/PlanFeatureInclusion data "
            "after migration - the AI feature tier gate may be running "
            "against stale or incomplete data until this is resolved."
        )
