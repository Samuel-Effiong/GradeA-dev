from rest_framework import serializers


class AssignmentGeneratorSerializer(serializers.Serializer):
    prompts = serializers.CharField(required=True)
