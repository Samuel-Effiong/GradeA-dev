from django.apps import AppConfig


class AuditConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "audit"

    def ready(self):
        from billing.immutable import register_append_only_guards

        from . import checks  # noqa: F401 - registers the audit system checks
        from .models import AuditEvent

        register_append_only_guards(AuditEvent)

        # Epic A S4: before/after history for the tracked models.
        from .history import connect

        connect()
