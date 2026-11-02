"""Session-epoch claim on issued JWTs (AUTHZ-T1/T2).

`CustomUser.token_epoch` is bumped on logout and on any credential change; a
token is only honoured while its `epoch` claim equals the user's current
epoch. A token with no claim predates this mechanism and counts as epoch 0,
which is what every existing user starts at, so deploying it signs nobody out.
"""

import uuid
from datetime import timedelta

from django.utils import timezone
from rest_framework_simplejwt.exceptions import InvalidToken
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.tokens import RefreshToken

EPOCH_CLAIM = "epoch"
# H-276: one id per sign-in (one browser/device), kept across refreshes.
SESSION_CLAIM = "sid"
REVOKED_MESSAGE = "Token has been revoked. Please log in again."


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
        token[SESSION_CLAIM] = str(uuid.uuid4())
        return token


def assert_epoch_current(token, user):
    if token_epoch_of(token) != user.token_epoch:
        raise InvalidToken(REVOKED_MESSAGE)


def session_id_of(token):
    """The session id a token belongs to, or None (minted before H-276, or
    malformed)."""
    try:
        return uuid.UUID(str(token.get(SESSION_CLAIM)))
    except (TypeError, ValueError, AttributeError):
        return None


def is_session_revoked(token):
    """True when the token's session was signed out (H-276). One indexed
    primary-key lookup, only for tokens that carry a session id."""
    from users.models import RevokedSession

    sid = session_id_of(token)
    return sid is not None and RevokedSession.objects.filter(sid=sid).exists()


def assert_session_live(token):
    if is_session_revoked(token):
        raise InvalidToken(REVOKED_MESSAGE)


def revoke_session(user, sid):
    """Sign out ONE session of `user` (H-276) and prune old revocations.

    A revoked session can mint nothing new (its refresh token is
    blacklisted), so every token carrying its id expires within the refresh
    lifetime; older rows can never match a live token again."""
    from users.models import RevokedSession

    RevokedSession.objects.get_or_create(sid=sid, defaults={"user": user})
    cutoff = timezone.now() - api_settings.REFRESH_TOKEN_LIFETIME - timedelta(days=1)
    RevokedSession.objects.filter(revoked_at__lt=cutoff).delete()
