from rest_framework import serializers

from .models import AuditEvent


class AuditEventSerializer(serializers.ModelSerializer):
    """Read-only projection of an AuditEvent row for the query API (§8)."""

    class Meta:
        model = AuditEvent
        fields = [
            "id",
            "occurred_at",
            "actor_id",
            "actor_role",
            "actor_email",
            "school_id",
            "department_id",
            "action",
            "target_type",
            "target_id",
            "outcome",
            "error_class",
            "reason_code",
            "trace_id",
            "client_correlation_id",
            "source_ip",
            "user_agent",
            "retention_class",
            "before",
            "after",
            "metadata",
        ]
        read_only_fields = fields
