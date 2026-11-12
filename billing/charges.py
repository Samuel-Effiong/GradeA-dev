"""What a teacher was charged: the one rule (SM ruling, R2 + S10, 2026-11-12).

A user's charges are the CreditUsageLog rows with is_refunded false whose
billed user, Coalesce(user_id, the wallet's user_id), is that user. It is
written without a join on the wallet: the wallet FK has no database
constraint, and an inner join would drop the logs whose wallet row is gone,
the very ones user_id is kept for. An old log without a user_id is found
through its wallet.

Used by the users list's credit usage (users/user_list.py) and the beta
tallies (billing/beta_tallies.py), so the two numbers match. Model classes
are parameters so migration 0075 can pass its historical models.
"""

from __future__ import annotations

from typing import Any, Iterable

from django.db.models import OuterRef, Q


def charged_q(
    *,
    user: Any = None,
    user_ids: Iterable[Any] | None = None,
    wallet_model=None,
) -> Q:
    """A Q over CreditUsageLog: the charges of `user` (a value or an
    OuterRef) or of every user in `user_ids`. Exactly one of the two."""
    if (user is None) == (user_ids is None):
        raise TypeError("charged_q takes exactly one of user or user_ids")
    if wallet_model is None:
        from billing.models import CreditWallet as wallet_model
    if user is not None:
        own = Q(user_id=user)
        # One level deeper inside the wallet subquery.
        owner = OuterRef(user) if isinstance(user, OuterRef) else user
        wallets = wallet_model.objects.filter(user_id=owner)
    else:
        ids = list(user_ids or ())
        own = Q(user_id__in=ids)
        wallets = wallet_model.objects.filter(user_id__in=ids)
    return Q(is_refunded=False) & (
        own | Q(user_id__isnull=True, wallet_id__in=wallets.values("pk"))
    )
