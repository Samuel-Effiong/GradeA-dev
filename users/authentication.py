"""Former server-side enforcement of `CustomUser.must_change_password`.

Enforcement (a 403 on everything but change-password/logout while
must_change_password=True) has been removed by product decision: a
license teacher, student or school admin with a system-generated
password may now use the API immediately. `must_change_password` itself
is kept on the model and surfaced via `CustomUserSerializer` so the
frontend can still show a nudge to change it.

`MustChangePasswordJWTAuthentication` is kept as a no-op wrapper around
`JWTAuthentication` - not deleted or renamed - because
`AutoGrader/settings.py`'s `DEFAULT_AUTHENTICATION_CLASSES` references it
by dotted path, and `users/schema.py` has a drf-spectacular
`OpenApiAuthenticationExtension` targeting it by string name
(`target_class = "users.authentication.MustChangePasswordJWTAuthentication"`).
Removing the class would break both.
"""

from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication

from users.tokens import token_epoch_of

# No longer read for enforcement (see MustChangePasswordJWTAuthentication's
# docstring), but view names a user with must_change_password=True could
# still reach even when the block was live - kept for any other code that
# still references it.
PASSWORD_CHANGE_ALLOWED_VIEW_NAMES = frozenset(
    {
        "auth-change-password",
        "auth-logout",
    }
)


class MustChangePasswordJWTAuthentication(JWTAuthentication):
    """JWTAuthentication plus the session-epoch check (AUTHZ-T1/T2).

    Name kept because AutoGrader/settings.py and users/schema.py reference it
    by dotted path (see module docstring). It no longer enforces
    must_change_password. A token is rejected when its epoch claim differs
    from the user's `token_epoch`, i.e. after logout or any credential change.
    No extra query: JWTAuthentication.get_user already loaded the user row.
    """

    def authenticate(self, request):
        result = super().authenticate(request)
        if result is None:
            return None
        user, token = result
        if token_epoch_of(token) != user.token_epoch:
            raise AuthenticationFailed(
                "Token has been revoked. Please log in again.", code="token_revoked"
            )
        return result
