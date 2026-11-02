"""
H-275: plain wording for live Stripe invoice transactions.

Stripe sends an internal `billing_reason` with every invoice. It used to be
pasted into the description users see ("Subscription invoice paid
(subscription_cycle)"). The phrases below were approved by the owner on
2026-11-02, American spelling. Change them only with a new owner approval.

License invoices use the same phrase with "Subscription" replaced by
"License". Any reason not listed (including a missing one, or one Stripe adds
later) uses the fallback, so a raw Stripe term never reaches a user.
"""

from typing import Optional

# billing_reason -> (payment received, payment failed)
_PHRASES = {
    "subscription_create": (
        "Subscription started – payment received",
        "Subscription start – payment failed",
    ),
    "subscription_cycle": (
        "Subscription renewal – payment received",
        "Subscription renewal – payment failed",
    ),
    "subscription_update": (
        "Plan change – payment received",
        "Plan change – payment failed",
    ),
    "subscription_threshold": (
        "Usage charge – payment received",
        "Usage charge – payment failed",
    ),
    "manual": ("Invoice – payment received", "Invoice – payment failed"),
}
_FALLBACK = ("Subscription payment received", "Subscription payment failed")


def invoice_description(
    *, billing_reason: Optional[str], paid: bool, license: bool
) -> str:
    received, failed = _PHRASES.get(billing_reason or "", _FALLBACK)
    text = received if paid else failed
    if license:
        text = text.replace("Subscription", "License")
    return text
