"""Epic C1: the tag API's shapes (plan 11 §3.4)."""

from rest_framework import serializers

from .models import Tag, TagType


class TagSerializer(serializers.ModelSerializer):
    """A tag as an assignment shows it, and as create, rename and set
    return it."""

    class Meta:
        model = Tag
        fields = ["id", "tag_type", "name"]
        read_only_fields = fields


class TagListSerializer(TagSerializer):
    """A row of the teacher's tag list: how many assignments carry it
    (annotated on the queryset; no query per row)."""

    usage_count = serializers.IntegerField(read_only=True)

    class Meta(TagSerializer.Meta):
        fields = ["id", "tag_type", "name", "usage_count"]
        read_only_fields = fields


class TagCreateSerializer(serializers.Serializer):
    # The name is checked by tags.normalize (TAG_NAME_MISSING and
    # TAG_NAME_TOO_LONG), so a missing one gets the approved wording.
    tag_type = serializers.ChoiceField(choices=TagType.choices)


class TagListQuerySerializer(serializers.Serializer):
    tag_type = serializers.ChoiceField(choices=TagType.choices, required=False)
    q = serializers.CharField(required=False, allow_blank=True, trim_whitespace=False)


class DeleteQuerySerializer(serializers.Serializer):
    expected_count = serializers.IntegerField(min_value=0)


class AssignmentTagsSerializer(serializers.Serializer):
    tag_ids = serializers.ListField(child=serializers.UUIDField(), allow_empty=True)
