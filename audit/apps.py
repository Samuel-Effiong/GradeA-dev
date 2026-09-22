from django.apps import AppConfig


class AuditConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "audit"

    def ready(self):
        from billing.immutable import register_append_only_guards

        from .models import AuditEvent

        register_append_only_guards(AuditEvent)
