"""
Row-lock order for credit wallets (H-181).

THE ORDER, EVERYWHERE: licence row, then wallet rows (by id), then bucket rows.

`CreditWallet.consume_credits` takes the wallet row FOR UPDATE and then the
wallet's buckets. A function that locks buckets first and writes the wallet
afterwards (`wallet.save`, or a bucket insert whose foreign-key check needs a
share lock on the wallet row) can therefore deadlock against a charge for the
same user: each holds what the other wants. Postgres aborts one of them, and
if that is the charge a finished AI answer is lost.

Every function that touches a wallet's buckets and writes the wallet calls
`lock_wallet_first` BEFORE its first bucket lock, inside its transaction. The
refund side did this already (`billing/credit_reversal.py`: "WALLETS FIRST,
THEN BUCKETS").

Licence rows come before wallets in the order; none of the callers here touch
one (H-182 deals with the charge side).
"""

from .models import CreditWallet


def lock_wallet_first(wallet):
    """Lock `wallet`'s row FOR UPDATE and return the locked instance.

    Must be called inside an open transaction (every caller is
    `@transaction.atomic`). Use the returned instance from here on: it is a
    fresh read taken under the lock, so a wallet loaded earlier cannot go
    stale between the read and the writes.
    """
    return CreditWallet.objects.select_for_update().get(pk=wallet.pk)
