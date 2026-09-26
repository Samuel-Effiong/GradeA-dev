"""
User-facing refusals for uploaded assignment and answer files.

Both are final answers about one file, not transient faults: the upload
tasks never retry them (assignments.tasks.UPLOAD_REFUSALS), and
AutoGrader.error_messages passes their message to the teacher or student
verbatim.
"""

from rest_framework.exceptions import ParseError


class InvalidUploadFileError(ParseError):
    """
    The file is not a readable image or PDF. The same bytes fail the same way
    on every attempt, so retrying can only re-run - and re-bill - work that
    cannot succeed. Subclasses ParseError so an API view that lets it
    propagate still answers 400.
    """


class UploadAlreadyInProgressError(Exception):
    """
    The identical file is already being turned into an assignment for this
    course by another request. Refused instead of extracted a second time,
    which would charge the teacher twice for one assignment.
    """
