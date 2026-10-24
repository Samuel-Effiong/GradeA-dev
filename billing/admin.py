"""
Django admin for the billing app.

Deliberately minimal: two ledgers are registered, both strictly
read-only.

- The Stripe webhook idempotency ledger: the record of which payment
  events were processed. Hand-editing it could re-run or suppress real
  money movement. Use `manage.py replay_stripe_events` to repair a failed
  event; that path is auditable and defaults to --dry-run.
- The licence Stripe-mutation intents (H-28): the record of every
  irreversible Stripe change made to a school's licence, and how far it
  got. Hand-editing a status could hide a real disagreement between
  Stripe and the application, so it is view-only here too.
"""

from django.contrib import admin

from .models import LicenseStripeMutationIntent, StripeEvent


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


@admin.register(LicenseStripeMutationIntent)
class LicenseStripeMutationIntentAdmin(admin.ModelAdmin):
    list_display = (
        "license_subscription",
        "operation",
        "status",
        "created_at",
        "updated_at",
        "escalated_at",
    )
    list_filter = ("status", "operation")
    search_fields = ("stripe_subscription_id", "license_subscription__school__name")
    ordering = ("-created_at",)
    date_hierarchy = "created_at"
    readonly_fields = (
        "id",
        "license_subscription",
        "operation",
        "status",
        "stripe_subscription_id",
        "requested_change",
        "stripe_result",
        "failure_reason",
        "performed_by",
        "escalated_at",
        "completed_at",
        "resolved_at",
        "resolved_by",
        "resolution_note",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        # A status edited by hand could mark a real Stripe/application
        # disagreement as resolved without anything having been fixed.
        return False

    def has_delete_permission(self, request, obj=None):
        # The row is the evidence that Stripe was changed.
        return False
