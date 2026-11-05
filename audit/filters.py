import uuid

import django_filters
from rest_framework.exceptions import ValidationError

from .enums import ActorRole, AuditOutcome, ErrorClass
from .models import AuditEvent


def _normalise_uuid(value):
    """Accept dashed or undashed, any case, trimmed.  Garbage → 400."""
    try:
        return uuid.UUID(value.strip())
    except (ValueError, AttributeError) as exc:
        raise ValidationError({"trace_id": "Not a valid UUID."}) from exc


class _BaseAuditEventFilter(django_filters.FilterSet):
    """Shared filter surface for both audit query endpoints (§8.1).

    `school_id` is deliberately NOT declared here. It lives only on
    `SuperAdminAuditEventFilter` - the School Admin endpoint hard-scopes its
    queryset to the caller's own school in the view, and a filter field that
    doesn't exist on this FilterSet is silently dropped by django-filter, so
    a client-supplied `school_id` query param on that endpoint has no effect
    at all rather than merely being validated and then overridden. See §8.1's
    "ignored... never merely validated" requirement.
    """

    time_from = django_filters.IsoDateTimeFilter(
        field_name="occurred_at", lookup_expr="gte"
    )
    time_to = django_filters.IsoDateTimeFilter(
        field_name="occurred_at", lookup_expr="lte"
    )
    actor_role = django_filters.ChoiceFilter(choices=ActorRole.choices)
    outcome = django_filters.ChoiceFilter(choices=AuditOutcome.choices)
    # H-293: where a failure came from. SYSTEM is the class that also reaches
    # Sentry, so ?error_class=SYSTEM is the "show me the crashes" view.
    error_class = django_filters.ChoiceFilter(choices=ErrorClass.choices)
    # Plan 08 D1: a STATE_CHANGE event names its route in metadata, so the
    # route is searchable without widening the closed action vocabulary.
    route = django_filters.CharFilter(field_name="metadata__route")
    trace_id = django_filters.CharFilter(method="filter_trace_id")
    target_id = django_filters.UUIDFilter(field_name="target_id")

    def filter_trace_id(self, queryset, name, value):
        return queryset.filter(trace_id=_normalise_uuid(value))

    class Meta:
        model = AuditEvent
        fields = [
            "actor_id",
            "actor_role",
            "action",
            "department_id",
            "outcome",
            "error_class",
            "reason_code",
        ]


class SuperAdminAuditEventFilter(_BaseAuditEventFilter):
    school_id = django_filters.UUIDFilter(field_name="school_id")

    class Meta(_BaseAuditEventFilter.Meta):
        fields = _BaseAuditEventFilter.Meta.fields + ["school_id"]


class SchoolAdminAuditEventFilter(_BaseAuditEventFilter):
    pass
