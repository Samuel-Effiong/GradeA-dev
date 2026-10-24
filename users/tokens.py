"""Session-epoch claim on issued JWTs (AUTHZ-T1/T2).

`CustomUser.token_epoch` is bumped on logout and on any credential change; a
token is only honoured while its `epoch` claim equals the user's current
epoch. A token with no claim predates this mechanism and counts as epoch 0,
which is what every existing user starts at, so deploying it signs nobody out.
"""

from rest_framework_simplejwt.exceptions import InvalidToken
from rest_framework_simplejwt.tokens import RefreshToken

EPOCH_CLAIM = "epoch"


def token_epoch_of(token):
    """The epoch a token was minted under (0 for tokens minted before it existed)."""
    return int(token.get(EPOCH_CLAIM, 0))


class EpochRefreshToken(RefreshToken):
    """RefreshToken that stamps the user's CURRENT epoch. The access token
    derived from it, and every token derived from a later refresh, inherit the
    claim."""

    @classmethod
    def for_user(cls, user):
        from users.models import CustomUser

        token = super().for_user(user)
        # Read from the database, not the instance: the instance may hold an
        # unresolved F() expression or a stale value.
        token[EPOCH_CLAIM] = CustomUser.objects.values_list(
            "token_epoch", flat=True
        ).get(pk=user.pk)
        return token


def assert_epoch_current(token, user):
    if token_epoch_of(token) != user.token_epoch:
        raise InvalidToken("Token has been revoked. Please log in again.")
