"""H-242: the shape of one upload flag, for the API description. Its own
module, with no import of this app's serializers, so the assignments app
can use it without an import cycle."""

from rest_framework import serializers

from ai_processor.extraction_flags import MESSAGES


class SubmissionFlagSerializer(serializers.Serializer):
    """One thing the answer reader noticed about an upload."""

    kind = serializers.ChoiceField(choices=sorted(MESSAGES))
    question_number = serializers.JSONField(allow_null=True)
    message = serializers.CharField()
    detail = serializers.CharField(allow_blank=True)
