from django.apps import AppConfig


class BillingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "billing"

    def ready(self):
        from django.db.models.signals import pre_delete

        import billing.checks  # noqa
        import billing.signals  # noqa
        from billing.holds import release_open_holds_on_delete
        from billing.models import CreditWallet

        # NOTES §21n: connected here, not at import time.
        pre_delete.connect(
            release_open_holds_on_delete,
            sender=CreditWallet,
            dispatch_uid="billing.holds.release_open_holds_on_delete",
        )
