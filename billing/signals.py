# billing/signals.py
#
# Intentionally empty (kept because billing.apps imports it).
#
# There used to be a post_save receiver on CreditUsageLog here that rolled
# consumption up into LicenseSubscription.total_credits_consumed. It never
# fired in practice: CreditWallet.consume_credits creates its usage logs
# with bulk_create(), which does not emit post_save. The rollup now happens
# explicitly in CreditWallet._record_license_consumption (and is reversed
# in SubscriptionService.refund_credits) — do not reintroduce a signal for
# billing-critical accounting; explicit calls can't be silently skipped by
# a bulk write.
