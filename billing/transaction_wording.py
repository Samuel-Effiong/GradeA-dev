"""
S18 (QA row 138): plain wording for billing transactions made by the backfill
command and by the data migration that rewrites its older rows.

The command used to write "Backfilled from checkout.session.completed (flow)",
which showed raw Stripe terms to users. The texts below are the same ones the
live webhook path writes (invoice phrases from H-275, owner-approved). Change
them only with a new owner approval.
"""

import re
from typing import Optional

from billing.invoice_wording import invoice_description

_PREFIX = "Backfilled from "
_INVOICE = re.compile(r"^Backfilled from invoice\.payment_(succeeded|failed) \((.*)\)$")
_CHECKOUT = re.compile(r"^Backfilled from checkout\.session\.completed \((.*)\)$")


def checkout_description(
    flow: str, *, plan_name: Optional[str], credits: Optional[int]
) -> str:
    if flow == "trial_to_paid":
        return f"Trial converted to {plan_name}" if plan_name else "Trial converted"
    if flow == "overage_block_purchase_checkout":
        if credits:
            return f"Credit top-up — {credits:,} AI credits"
        return "Credit top-up"
    if flow == "individual_upgrade_checkout":
        return "Plan change – payment received"
    if flow == "license_create":
        if plan_name:
            return f"License created — {plan_name}"
        return invoice_description(billing_reason=None, paid=True, license=True)
    # individual_checkout, individual_subscribe
    if plan_name:
        return f"New subscription — {plan_name}"
    return invoice_description(billing_reason=None, paid=True, license=False)


def rewrite_backfilled_description(
    old: str, *, plan_name: Optional[str], license: bool
) -> Optional[str]:
    """New text for an old "Backfilled from ..." description, else None."""
    if not old or not old.startswith(_PREFIX):
        return None
    match = _INVOICE.match(old)
    if match:
        reason = match.group(2)
        return invoice_description(
            billing_reason=None if reason in ("", "None") else reason,
            paid=match.group(1) == "succeeded",
            license=license,
        )
    match = _CHECKOUT.match(old)
    if match:
        # The quantity is not stored on the row, so a top-up has no number.
        return checkout_description(match.group(1), plan_name=plan_name, credits=None)
    return None
