from django.apps import AppConfig


class BillingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "billing"

    def ready(self):
        from django.db.models.signals import post_delete, pre_delete

        import billing.checks  # noqa
        import billing.signals  # noqa
        from billing import holds
        from billing.holds import (
            finish_job_left_empty_on_item_delete,
            release_open_holds_on_delete,
            return_hold_on_item_delete,
        )
        from billing.models import CreditWallet
        from students.models import BackgroundProcessingTask

        # NOTES §21n: connected here, not at import time.
        pre_delete.connect(
            release_open_holds_on_delete,
            sender=CreditWallet,
            dispatch_uid="billing.holds.release_open_holds_on_delete",
        )
        # B1b FB-1 (NOTES §1.1): a deleted task row returns its hold.
        pre_delete.connect(
            return_hold_on_item_delete,
            sender=BackgroundProcessingTask,
            dispatch_uid="billing.holds.return_hold_on_item_delete",
        )
        # NOTES §1.20: C4 runs after the batch delete, so the rows the same
        # delete removes are gone whatever order the collector visits them.
        post_delete.connect(
            finish_job_left_empty_on_item_delete,
            sender=BackgroundProcessingTask,
            dispatch_uid="billing.holds.finish_job_left_empty_on_item_delete",
        )
        # B1b-9 Q9-6: the emergency state is said once at start-up.
        holds.warn_if_draining_with_holds_off()
