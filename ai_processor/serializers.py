import re

from rest_framework import serializers

# A prompt that's "just a link" (e.g. a source article the teacher wants an
# assignment built from) is legitimate on its own - fetch_url_content lets
# the model pull real content from it - so URL-bearing prompts are exempt
# from the word-count floor below.
_URL_PATTERN = re.compile(r"(https?://\S+|www\.\S+)", re.IGNORECASE)

# Below this many words, a prompt can't possibly convey enough subject
# matter for a meaningful assignment. Deliberately generous - this only
# exists to catch empty/near-empty input before any AI credits are spent.
# Judging borderline-but-still-insufficient prompts (e.g. "The sky is red",
# which is 4 words) is the AI's job, not this regex's - see the
# "Insufficient Input Handling" section of ASSIGNMENT_GENERATION_PROMPT_6.txt.
_MIN_PROMPT_WORDS = 3


class AssignmentGeneratorSerializer(serializers.Serializer):
    prompt = serializers.CharField(required=True, allow_blank=False)
    session_id = serializers.UUIDField(required=False, allow_null=True)

    def validate_prompt(self, value):
        stripped = value.strip()
        if not stripped:
            raise serializers.ValidationError(
                "Please describe what you'd like the assignment to cover."
            )

        if _URL_PATTERN.search(stripped):
            return value

        if len(stripped.split()) < _MIN_PROMPT_WORDS:
            raise serializers.ValidationError(
                "Your prompt is too short for us to generate a meaningful "
                "assignment. Please add a bit more detail about the topic, "
                "subject, or learning goal you'd like covered."
            )

        return value
