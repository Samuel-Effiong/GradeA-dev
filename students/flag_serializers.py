"""H-242: the shape of one upload flag, for the API description. Its own
module, with no import of this app's serializers, so the assignments app
can use it without an import cycle.

H-253: a flag is either an answer's own (`review_flag`, inside each entry
of `answers`) or the paper's (`upload_mismatch`); neither carries the
question number, which the answer already has."""

from rest_framework import serializers

from ai_processor.extraction_flags import MESSAGES


class ReviewFlagSerializer(serializers.Serializer):
    """One thing the answer reader noticed about an upload."""

    kind = serializers.ChoiceField(choices=sorted(MESSAGES))
    message = serializers.CharField()
    detail = serializers.CharField(allow_blank=True)


#: The help text of every `upload_mismatch` field.
UPLOAD_MISMATCH_HELP = (
    "H-253: null when the upload is for this assignment. Otherwise "
    "{kind: SUBMISSION_MISMATCH, message, detail}: the reader found nothing "
    "on the upload that answers this assignment. Each question's own flag "
    "is `review_flag` inside its entry of `answers` (null when there is "
    "nothing to check), with the same three keys."
)
