from rest_framework import serializers


class AssignmentGeneratorSerializer(serializers.Serializer):
    prompt = serializers.CharField(required=True)
