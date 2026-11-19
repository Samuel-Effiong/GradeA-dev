"""Plain serializers with explicit field lists, nothing nested from any
other app (plan 5.3): a teacher's courses, students or credits cannot reach
a response from here because no serializer here can name them."""

from rest_framework import serializers


class DepartmentWriteSerializer(serializers.Serializer):
    # The rules (empty, too long, taken) live in the service so every route
    # answers with the one approved message; the serializer only bounds input.
    name = serializers.CharField(
        required=False, allow_blank=True, default="", max_length=1000
    )
    description = serializers.CharField(
        required=False, allow_blank=True, default=None, max_length=1000
    )


class DepartmentRenameSerializer(serializers.Serializer):
    name = serializers.CharField(required=False, allow_blank=True, max_length=1000)
    description = serializers.CharField(
        required=False, allow_blank=True, max_length=1000
    )


class AddMembersSerializer(serializers.Serializer):
    teacher_ids = serializers.ListField(
        child=serializers.CharField(allow_blank=True, max_length=64),
        allow_empty=False,
        max_length=1000,
    )


class MemberEditSerializer(serializers.Serializer):
    can_edit = serializers.BooleanField()


class DepartmentRowSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    description = serializers.CharField()
    member_count = serializers.IntegerField()
    created_at = serializers.DateTimeField()


class AdminMemberRowSerializer(serializers.Serializer):
    membership_id = serializers.UUIDField()
    teacher_id = serializers.UUIDField()
    name = serializers.CharField()
    email = serializers.CharField()
    can_edit = serializers.BooleanField()
    added_at = serializers.DateTimeField()


class MyDepartmentSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    can_edit = serializers.BooleanField()


class RosterRowSerializer(serializers.Serializer):
    name = serializers.CharField()
    role = serializers.CharField()
