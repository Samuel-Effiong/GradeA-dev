"""Server-side enforcement of `CustomUser.must_change_password`.

`DEFAULT_PERMISSION_CLASSES` is not a reliable enforcement point for this:
several viewsets (e.g. `CustomUserViewSet`) override `permission_classes`/
`get_permissions()` per-view, which bypasses the global default entirely.
`DEFAULT_AUTHENTICATION_CLASSES` is applied uniformly instead - nothing in
this codebase overrides `authentication_classes` per-view - so the check
lives here, wrapping the JWT authenticator every authenticated request
already goes through.
"""

from rest_framework.exceptions import APIException
from rest_framework_simplejwt.authentication import JWTAuthentication

# View names (DRF router: f"{basename}-{url_name}") a user with
# must_change_password=True may still reach - they need a way to change
# their password, and a way to log out. Nothing else.
PASSWORD_CHANGE_ALLOWED_VIEW_NAMES = frozenset(
    {
        "auth-change-password",
        "auth-logout",
    }
)


class PasswordChangeRequired(APIException):
    status_code = 403
    default_detail = "This account's password must be changed before it can be used."
    default_code = "password_change_required"


class MustChangePasswordJWTAuthentication(JWTAuthentication):
    """Wraps JWTAuthentication: once a user is resolved, rejects everything
    except the change-password/logout allow-list while
    `must_change_password` is True.

    Known, deliberate gap: `SessionAuthentication` (also in
    DEFAULT_AUTHENTICATION_CLASSES, for the browsable API) is NOT wrapped,
    so a session-authenticated request bypasses this check entirely. Left
    as-is because nothing in the real API client uses session auth today -
    only worth closing if that changes.
    """

    def authenticate(self, request):
        result = super().authenticate(request)
        if result is None:
            return None

        user, validated_token = result
        if not getattr(user, "must_change_password", False):
            return result

        resolver_match = getattr(request, "resolver_match", None)
        view_name = getattr(resolver_match, "view_name", None)
        if view_name in PASSWORD_CHANGE_ALLOWED_VIEW_NAMES:
            return result

        raise PasswordChangeRequired()
