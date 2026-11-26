"""Analytics Step 4 (owner, 2026-11-25): paid_since and ended_at on
UserSubscription, the history churn is read from, and
BillingSettings.subscription_history_from, from when that history is exact.

From now on UserSubscription.save stamps both fields. This migration fills
them once for the rows that already exist (frozen rules: a migration must not
import app code that can change later). Reversing does nothing.

subscription_history_from = the first audited subscription change (Epic A
S4 records every is_active / is_trial change from then on), or now when there
is none. After it every end and conversion is exact; a time read from another
source is capped at it, since anything not on the audit trail happened before.

ended_at, for an inactive row, from the first source that has it:
  1. the audit event that switched is_active true -> false;
  2. Stripe's customer.subscription.deleted event for its Stripe id
     (payload object ended_at, else canceled_at);
  3. the created_at of the same user's next (strictly later) subscription
     (a switch ends the old row and creates the new one in one
     transaction);
  4. the row's updated_at (the save that ended it; ended rows are rarely
     saved again).
paid_since:
  - a trial now: none;
  - born a trial (its TRIAL credit grant names the row): the audit event that
    switched is_trial true -> false while still active (an event that also
    switched is_active off is an expiry: none); with no event, converted if
    trial_end was cleared (an expiry keeps it), at the trial bucket's
    expires_at (the conversion sets it to that moment); else none;
  - born paid: created_at.
"""

from datetime import datetime
from datetime import timezone as dt_timezone

from django.db import migrations, models
from django.utils import timezone

BATCH = 500


def _stripe_time(value):
    return datetime.fromtimestamp(int(value), tz=dt_timezone.utc)


def _audit_history(AuditEvent):
    ends, conversions, expiries = {}, {}, set()
    events = AuditEvent.objects.filter(
        action="SUBSCRIPTION_CHANGE",
        target_type="UserSubscription",
        target_id__isnull=False,
    ).order_by("occurred_at")
    for target_id, occurred_at, before, after in events.values_list(
        "target_id", "occurred_at", "before", "after"
    ).iterator(chunk_size=BATCH):
        before, after = before or {}, after or {}
        if before.get("is_active") is True and after.get("is_active") is False:
            ends[target_id] = occurred_at
        if before.get("is_trial") is True and after.get("is_trial") is False:
            if after.get("is_active") is False:
                expiries.add(target_id)
            else:
                conversions.setdefault(target_id, occurred_at)
    return ends, conversions, expiries


def _stripe_ends(StripeEvent):
    ends = {}
    payloads = StripeEvent.objects.filter(
        event_type="customer.subscription.deleted"
    ).values_list("payload", flat=True)
    for payload in payloads.iterator(chunk_size=BATCH):
        obj = (payload or {}).get("object") or {}
        moment = obj.get("ended_at") or obj.get("canceled_at")
        if obj.get("id") and moment:
            ends[obj["id"]] = _stripe_time(moment)
    return ends


def _trial_buckets(CreditLedger):
    """Row id (str) -> its TRIAL bucket's expires_at."""
    found = {}
    grants = CreditLedger.objects.filter(
        ledger_type="GRANT", bucket__bucket_type="TRIAL"
    ).values_list("metadata", "bucket__expires_at")
    for metadata, expires_at in grants.iterator(chunk_size=BATCH):
        sub_id = (metadata or {}).get("subscription_id")
        if sub_id:
            found[str(sub_id)] = expires_at
    return found


def backfill(apps, schema_editor):
    UserSubscription = apps.get_model("billing", "UserSubscription")
    BillingSettings = apps.get_model("billing", "BillingSettings")
    AuditEvent = apps.get_model("audit", "AuditEvent")
    StripeEvent = apps.get_model("billing", "StripeEvent")
    CreditLedger = apps.get_model("billing", "CreditLedger")

    first = (
        AuditEvent.objects.filter(
            action="SUBSCRIPTION_CHANGE", target_type="UserSubscription"
        )
        .order_by("occurred_at")
        .values_list("occurred_at", flat=True)
        .first()
    )
    history_from = first or timezone.now()
    BillingSettings.objects.update_or_create(
        pk=1, defaults={"subscription_history_from": history_from}
    )

    audited_ends, conversions, expiries = _audit_history(AuditEvent)
    stripe_ends = _stripe_ends(StripeEvent)
    trial_buckets = _trial_buckets(CreditLedger)

    rows = list(
        UserSubscription.objects.order_by("user_id", "created_at", "pk").values_list(
            "pk",
            "user_id",
            "created_at",
            "updated_at",
            "is_active",
            "is_trial",
            "trial_end",
            "stripe_subscription_id",
            "paid_since",
            "ended_at",
        )
    )
    changed = []
    for index, row in enumerate(rows):
        (pk, user_id, created_at, updated_at, is_active, is_trial, trial_end,
         stripe_id, paid_since, ended_at) = row  # fmt: skip
        next_created = None
        for later in rows[index + 1 :]:
            if later[1] != user_id:
                break
            if later[2] > created_at:
                next_created = later[2]
                break

        new_paid = paid_since
        if new_paid is None and not is_trial:
            if str(pk) not in trial_buckets:
                new_paid = created_at
            elif pk in conversions:
                new_paid = conversions[pk]
            elif pk not in expiries and trial_end is None:
                bucket_expiry = trial_buckets[str(pk)]
                if bucket_expiry is not None:
                    new_paid = min(bucket_expiry, history_from)

        new_end = ended_at
        if new_end is None and not is_active:
            if pk in audited_ends:
                new_end = audited_ends[pk]
            else:
                moment = stripe_ends.get(stripe_id) or next_created or updated_at
                new_end = min(moment, history_from)

        if (new_paid, new_end) != (paid_since, ended_at):
            changed.append(
                UserSubscription(pk=pk, paid_since=new_paid, ended_at=new_end)
            )
    for start in range(0, len(changed), BATCH):
        UserSubscription.objects.bulk_update(
            changed[start : start + BATCH], ["paid_since", "ended_at"]
        )


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0091_rescore_two_intent_signals"),
        ("audit", "0004_alter_auditevent_retention_class"),
    ]

    operations = [
        migrations.AddField(
            model_name="billingsettings",
            name="subscription_history_from",
            field=models.DateTimeField(
                blank=True,
                editable=False,
                help_text="From when subscription start and end times are exact (the audit trail's first subscription change). Churn before it is not shown.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="usersubscription",
            name="ended_at",
            field=models.DateTimeField(
                blank=True,
                help_text="When is_active went from true to false: the subscription actually ended (not the cancel request, see cancelled_at). Null while active.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="usersubscription",
            name="paid_since",
            field=models.DateTimeField(
                blank=True,
                help_text="When this subscription became a non-trial plan: created as one, or a trial converted on this row. Null while a trial, and for a trial that ended without converting.",
                null=True,
            ),
        ),
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
