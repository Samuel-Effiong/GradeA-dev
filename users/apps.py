from django.apps import AppConfig


class UsersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "users"

    def ready(self):
        # drf-spectacular's OpenApiAuthenticationExtension subclasses
        # self-register on import - the module has to actually be
        # imported somewhere for the extension to take effect. See
        # assignments/tests_schema_extension.py for the sibling bug this
        # repo already hit once from relying on SPECTACULAR_SETTINGS'
        # "EXTENSIONS" key instead, which drf-spectacular silently ignores.
        from . import schema  # noqa: F401
        from . import signals  # noqa: F401
