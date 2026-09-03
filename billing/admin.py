"""
Django admin for the billing app.

Deliberately minimal: only the Stripe webhook idempotency ledger is
registered, and it is strictly read-only. The ledger is the record of
which payment events were processed — hand-editing it could re-run or
suppress real money movement, so add/change/delete are all disabled and
every field is read-only. Use `manage.py replay_stripe_events` to repair
a failed event; that path is auditable and defaults to --dry-run.
"""

from django.contrib import admin

from .models import StripeEvent


@admin.register(StripeEvent)
class StripeEventAdmin(admin.ModelAdmin):
    list_display = (
        "stripe_event_id",
        "event_type",
        "status",
        "attempts",
        "processed_at",
        "completed_at",
    )
    list_filter = ("status", "event_type")
    search_fields = ("stripe_event_id",)
    ordering = ("-processed_at",)
    date_hierarchy = "processed_at"
    readonly_fields = (
        "id",
        "stripe_event_id",
        "event_type",
        "status",
        "attempts",
        "processed_at",
        "claimed_at",
        "completed_at",
        "last_error",
        "payload",
    )

    def has_add_permission(self, request):
        # Rows are only ever created by an authenticated Stripe delivery.
        return False

    def has_change_permission(self, request, obj=None):
        # View-only: the ledger decides whether money-moving handlers run.
        return False

    def has_delete_permission(self, request, obj=None):
        # Deleting a row is precisely the bug this ledger was fixed to stop
        # (see billing/webhooks.py) — a deleted event is one nobody can
        # prove happened.
        return False
