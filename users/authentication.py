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

from rest_framework_simplejwt.authentication import JWTAuthentication

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
    """No-op wrapper kept only so its dotted path and schema extension
    (see module docstring) keep resolving. Behaves exactly like plain
    JWTAuthentication.
    """

    def authenticate(self, request):
        result = super().authenticate(request)
        return result
