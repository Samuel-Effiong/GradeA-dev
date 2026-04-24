from rest_framework import serializers


class AssignmentGeneratorSerializer(serializers.Serializer):
    prompt = serializers.CharField(required=True)
    session_id = serializers.UUIDField(required=False, allow_null=True)
