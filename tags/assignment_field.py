"""Epic C1: the read-only `tags` field on the teacher's assignment list and
detail (plan 11 §3.4).

Present only while TAGS_ENABLED is on, so a dark build's assignment
responses stay byte-identical (assignments/tests_c1_dark_snapshot.py).
Students never get it (their serializers do not use this mixin), and
admins list no assignments. The rows come from `prefetch_tags`, so a list
costs one extra query, not one per row.
"""

from typing import TYPE_CHECKING

from django.conf import settings
from django.db.models import Prefetch
from rest_framework import serializers

from .models import AssignmentTag

PREFETCH_TO = "prefetched_tag_links"


def tags_enabled():
    return bool(getattr(settings, "TAGS_ENABLED", False))


def prefetch_tags(queryset):
    """The teacher's assignment queryset, with its tag links when the flag
    is on (unchanged otherwise)."""
    if not tags_enabled():
        return queryset
    return queryset.prefetch_related(
        Prefetch(
            "tag_links",
            queryset=AssignmentTag.objects.select_related("tag").order_by(
                "tag__tag_type", "tag__normalized_name", "tag_id"
            ),
            to_attr=PREFETCH_TO,
        )
    )


if TYPE_CHECKING:
    _SerializerBase = serializers.Serializer
else:
    _SerializerBase = object


class AssignmentTagsFieldMixin(_SerializerBase):
    def get_fields(self):
        fields = super().get_fields()
        if tags_enabled():
            fields["tags"] = serializers.SerializerMethodField()
        return fields

    def get_tags(self, obj):
        links = getattr(obj, PREFETCH_TO, None)
        if links is None:
            links = obj.tag_links.select_related("tag").order_by(
                "tag__tag_type", "tag__normalized_name", "tag_id"
            )
        return [
            {
                "id": str(link.tag_id),
                "tag_type": link.tag.tag_type,
                "name": link.tag.name,
            }
            for link in links
        ]
