"""users/schema.py - pins MustChangePasswordJWTScheme as active.

drf-spectacular registers an OpenApiAuthenticationExtension when the
module defining it is imported - subclassing is the registration, same
mechanism assignments/tests_schema_extension.py documents for the
(deliberately still inactive) polymorphic assignment extension.

Wiring MustChangePasswordJWTAuthentication (users/authentication.py) in as
DEFAULT_AUTHENTICATION_CLASSES[0] broke drf-spectacular's ability to match
it to simplejwt's built-in Bearer-auth scheme, since the class-path match
is exact, not inheritance-aware - the docs page silently lost its
Bearer-token "Authorize" box across every endpoint. users/schema.py fixes
that by registering the same scheme under the wrapper's own class path.

This test pins the extension as actually active (unlike the sibling bug),
so a future refactor that accidentally un-wires the `from . import schema`
import in users/apps.py.ready() fails loudly here instead of only showing
up as a silently missing box in the docs page.
"""

from django.test import TestCase
from drf_spectacular.extensions import OpenApiAuthenticationExtension

from users.authentication import MustChangePasswordJWTAuthentication


class MustChangePasswordJWTSchemeRegistrationTest(TestCase):
    def test_extension_is_registered_for_the_wrapper_authenticator(self):
        match = OpenApiAuthenticationExtension.get_match(
            MustChangePasswordJWTAuthentication()
        )
        self.assertIsNotNone(
            match,
            "No OpenApiAuthenticationExtension resolved for "
            "MustChangePasswordJWTAuthentication - either users/schema.py "
            "was removed, or users/apps.py.ready() no longer imports it, "
            "so the docs page's Bearer-token box will silently disappear "
            "again.",
        )

    def test_extension_targets_the_wrapper_class_exactly(self):
        # target_class starts life as an import-path string on the class
        # (see users/schema.py) but drf-spectacular resolves it in place
        # to the actual class the first time it's matched against.
        match = OpenApiAuthenticationExtension.get_match(
            MustChangePasswordJWTAuthentication()
        )
        self.assertIs(match.target_class, MustChangePasswordJWTAuthentication)
