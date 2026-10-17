from drf_spectacular.contrib.rest_framework_simplejwt import SimpleJWTScheme


class MustChangePasswordJWTScheme(SimpleJWTScheme):
    """Registers the same OpenAPI bearer-auth scheme drf-spectacular's
    built-in SimpleJWTScheme provides for simplejwt's JWTAuthentication,
    but for MustChangePasswordJWTAuthentication.

    drf-spectacular's authenticator resolution is an exact class-path
    match, so wrapping JWTAuthentication (see authentication.py) silently
    dropped the Bearer-token box from the API docs page - the generator
    could no longer resolve the authenticator to any scheme.
    """

    target_class = "users.authentication.MustChangePasswordJWTAuthentication"
