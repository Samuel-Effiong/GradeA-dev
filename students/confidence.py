"""AI confidence averaging, in one place.

`StudentSubmission.grading_confidence`/`extraction_confidence` both default
to 0, not null, until a submission is actually graded (students/models.py).
Averaging over all submissions therefore silently drags the number down
with ungraded 0s, and filtering on `<field>__isnull=False` is a no-op since
neither field is ever null. `graded_at` is the real "was this graded"
signal, so every confidence average must filter on that instead. Found
drifting into two different wrong shapes twice (H-238); this is the one
place both call it from.
"""

from django.db.models import Avg


def average_confidence_from(submissions, field_name):
    """The average of `field_name` over `submissions` that were actually
    graded, or None if none were.
    """
    return submissions.filter(graded_at__isnull=False).aggregate(avg=Avg(field_name))[
        "avg"
    ]
