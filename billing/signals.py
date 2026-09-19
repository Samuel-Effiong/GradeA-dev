# billing/signals.py
#
# There used to be a post_save receiver on CreditUsageLog here that rolled
# consumption up into LicenseSubscription.total_credits_consumed. It never
# fired in practice: CreditWallet.consume_credits creates its usage logs
# with bulk_create(), which does not emit post_save. The rollup now happens
# explicitly in CreditWallet._record_license_consumption (and is reversed
# in SubscriptionService.refund_credits) — do not reintroduce a signal for
# billing-critical ACCOUNTING; explicit calls can't be silently skipped by
# a bulk write.
#
# The receiver below is unrelated to that concern: it is cache invalidation,
# not accounting, and every CreditBucket write site in this codebase uses
# `.objects.create()` or `.save()` (grep confirms it - no bulk_create/
# bulk_update on this model), so post_save fires reliably for it.

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from AutoGrader.cache_generation import SCOPE_USER, bump_many
from billing.models import CreditBucket


@receiver([post_save, post_delete], sender=CreditBucket)
def clear_credit_bucket_cache(sender, instance, **kwargs):
    # H-1 Stage 3 (pre-existing staleness P5): CreditBucket had no receiver
    # at all, so a grant, a licence-enrolment bucket, or ordinary
    # consumption never reached the owning user's cached `users/me`
    # payload, which nests `credit_wallet.total_remaining_credits`.
    #
    # H-1 Stage 3 (gap #4): the wallet is also rendered to every OTHER
    # viewer of the owner's `users/<pk>` - their teachers, school admins
    # and the superadmins - each keyed on the viewer's own generation.
    from users.signals import superadmin_user_ids, user_payload_viewer_scopes

    wallet = getattr(instance, "wallet", None)
    user_id = getattr(wallet, "user_id", None) if wallet is not None else None
    if user_id is None:
        return
    school_id = getattr(getattr(wallet, "user", None), "school_id", None)
    scopes = [(SCOPE_USER, user_id)]
    scopes.extend(user_payload_viewer_scopes([user_id], [school_id]))
    scopes.extend((SCOPE_USER, admin_id) for admin_id in superadmin_user_ids())
    bump_many(list(dict.fromkeys(scopes)))
