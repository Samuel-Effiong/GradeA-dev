"""The upload quote routes (plan rev 3 section 3; owner change 2026-11-20).

Teacher-only and deliberately NOT behind HasCreditBalance: a teacher at 0
must get a verdict (BE-B-04). While UPLOAD_QUOTES_ENABLED is off every route
answers as an unknown URL does, before authentication. Foreign, unknown or
malformed ids all answer the one house 404.

POST /upload-quotes/ is multipart and does create + files in one request: the
frontend sends every file at once and does no checks of its own, so the server
does every check and one bad file never fails the others. A repeat of the same
`Idempotency-Key` returns the same quote (200) and stores nothing again.
Without a key a retry makes a new quote (bounded by the open-quote limit and
the quote lifetime).
"""

import uuid
from typing import TYPE_CHECKING

from django.conf import settings
from django.http import Http404
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.exceptions import ParseError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from assignments.models import Assignment
from assignments.upload_door import batch_refusal_response, door_balance
from AutoGrader import uploads
from AutoGrader.handlers import json_404
from classrooms.models import Topic, reachable_courses, teacher_course_access_q
from classrooms.permissions import IsTeacher
from students import upload_quote_service as service
from students.models import UploadQuote
from students.upload_quote_confirm import confirm as confirm_quote

if TYPE_CHECKING:
    _ViewBase = APIView
else:
    _ViewBase = object

#: The text get_object_or_404 gives, so every 404 of these routes is one body.
NOT_FOUND = "No UploadQuote matches the given query."
NO_FILES = "No files were uploaded. Please try again"
#: PROPOSED wording (docs/evidence/upload-quotes/WORDING.md).
TOO_MANY_FILES = (
    "You can check up to {limit} files at a time. Remove some and try again."
)


class UploadQuoteCreateThrottle(UserRateThrottle):
    scope = "upload_quote_create"


class UploadQuoteReadThrottle(UserRateThrottle):
    scope = "upload_quote_read"


class DarkUntilEnabled(_ViewBase):
    """While UPLOAD_QUOTES_ENABLED is off, answer as an unknown URL does."""

    def dispatch(self, request, *args, **kwargs):
        if not getattr(settings, "UPLOAD_QUOTES_ENABLED", False):
            return json_404(request)
        return super().dispatch(request, *args, **kwargs)


class CreateSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=UploadQuote.Kind.choices)
    assignment_id = serializers.UUIDField(required=False)
    course_id = serializers.UUIDField(required=False)
    topic_id = serializers.UUIDField(required=False, allow_null=True)

    def validate(self, attrs):
        if attrs["kind"] == UploadQuote.Kind.ANSWER_PAPERS:
            if "assignment_id" not in attrs:
                raise serializers.ValidationError({"assignment_id": "Required."})
        elif "course_id" not in attrs:
            raise serializers.ValidationError({"course_id": "Required."})
        return attrs


FILE_ENTRY = inline_serializer(
    "UploadQuoteFileEntry",
    {
        "id": serializers.UUIDField(allow_null=True),
        "position": serializers.IntegerField(),
        "state": serializers.ChoiceField(
            choices=[
                "STORING",
                "STORED",
                "MEASURING",
                "MEASURED",
                "REFUSED",
                "NOT_KEPT",
            ]
        ),
        "readable": serializers.BooleanField(allow_null=True),
        "reason_code": serializers.CharField(allow_null=True),
        "pages": serializers.IntegerField(allow_null=True),
        "calls": serializers.IntegerField(allow_null=True),
        "high": serializers.IntegerField(
            allow_null=True, help_text="This file's own high figure, display credits."
        ),
        "already_uploaded": serializers.BooleanField(),
        "duplicate_of": serializers.UUIDField(allow_null=True),
    },
    many=True,
)
ESTIMATE = inline_serializer(
    "UploadQuoteEstimate",
    {
        "p50": serializers.IntegerField(),
        "p90": serializers.IntegerField(),
        "verdict": serializers.ChoiceField(
            choices=["SUFFICIENT", "MARGINAL", "INSUFFICIENT"]
        ),
        "spendable": serializers.IntegerField(),
        "projected_remaining_p50": serializers.IntegerField(),
        "projected_remaining_p90": serializers.IntegerField(),
        "source": serializers.CharField(),
        "shape_basis": serializers.CharField(),
        "sample_size": serializers.IntegerField(),
        "n": serializers.IntegerField(),
    },
)
QUOTE_REPLY = inline_serializer(
    "UploadQuote",
    {
        "id": serializers.UUIDField(),
        "kind": serializers.ChoiceField(choices=UploadQuote.Kind.choices),
        "state": serializers.ChoiceField(choices=UploadQuote.State.choices),
        "expires_at": serializers.DateTimeField(),
        "lifetime_minutes": serializers.IntegerField(),
        "ready": serializers.BooleanField(),
        "measured": serializers.IntegerField(),
        "total": serializers.IntegerField(),
        "session_id": serializers.UUIDField(allow_null=True),
        "estimate": ESTIMATE,
        "would_park": serializers.ListField(child=serializers.IntegerField()),
        "files": FILE_ENTRY,
    },
)
SUMMARY_REPLY = inline_serializer(
    "UploadQuoteSummary",
    {
        "id": serializers.UUIDField(),
        "kind": serializers.ChoiceField(choices=UploadQuote.Kind.choices),
        "created_at": serializers.DateTimeField(),
        "expires_at": serializers.DateTimeField(),
        "lifetime_minutes": serializers.IntegerField(),
        "file_count": serializers.IntegerField(),
        "ready": serializers.BooleanField(),
    },
    many=True,
)
CONFIRM_REPLY = inline_serializer(
    "UploadQuoteConfirmed",
    {
        "quote_id": serializers.UUIDField(),
        "session_id": serializers.UUIDField(),
        "message": serializers.CharField(),
        "tasks": inline_serializer(
            "UploadQuoteConfirmedTask",
            {
                "file_name": serializers.CharField(),
                "task_id": serializers.CharField(allow_null=True),
                "item_id": serializers.UUIDField(),
                "status": serializers.ChoiceField(
                    choices=["ACCEPTED", "PARKED", "REFUSED"]
                ),
                "reason_code": serializers.CharField(allow_null=True),
            },
            many=True,
        ),
    },
)


def refuse_oversize_request(request):
    """FILE_TOO_LARGE (413, dimension bytes) when the request's declared
    length is over the cap, before anything reads the body (the Fixes Worker's
    request cap on the three upload routes; its own wording is Q8: this uses
    the existing sentence with no file name until that lands)."""
    cap = int(getattr(settings, "UPLOAD_REQUEST_MAX_BYTES", 500 * 1024 * 1024))
    try:
        length = int(request.META.get("CONTENT_LENGTH") or 0)
    except ValueError:
        return
    if length > cap:
        raise uploads.PayloadTooLarge(
            params={"actual": length, "limit": cap, "dimension": "bytes"},
            display={
                "file_name": "This upload",
                "actual": uploads.human_size(length),
                "limit": uploads.human_size(cap),
            },
        )


def nothing_spendable(user):
    """The existing 402 when the teacher has no spendable credit (balance
    less live holds, the door's own figure), else None. Checked before any
    byte is stored or row made (owner Q3 = C)."""
    wallet = getattr(user, "credit_wallet", None)
    if wallet is not None and door_balance(wallet) <= 0:
        return batch_refusal_response(user)
    return None


def own_quote(request, pk):
    """The teacher's quote whose course they can still reach, else the house
    404 (unknown, malformed, another teacher's or another school's)."""
    try:
        pk = uuid.UUID(str(pk))
    except ValueError:
        raise Http404(NOT_FOUND) from None
    quote = get_object_or_404(
        UploadQuote.objects.select_related("teacher", "assignment", "course"),
        pk=pk,
        teacher=request.user,
    )
    course_id = quote.course_id or (
        quote.assignment.course_id if quote.assignment else None
    )
    if not reachable_courses(request.user).filter(pk=course_id).exists():
        raise Http404(NOT_FOUND)
    return quote


@extend_schema(tags=["Upload quotes"])
class UploadQuoteCollection(DarkUntilEnabled, APIView):
    permission_classes = [IsAuthenticated, IsTeacher]

    def get_throttles(self):
        cls = (
            UploadQuoteCreateThrottle
            if self.request.method == "POST"
            else UploadQuoteReadThrottle
        )
        return [cls()]

    @extend_schema(
        summary="Create an upload quote with its files",
        description=(
            "Multipart: `kind`, `assignment_id` (answer_papers) or `course_id` "
            "and optional `topic_id` (assignment_files), and `files` (one or "
            "many; list order is the order the files are walked). The server "
            "checks every file; a refused file is its own entry with its "
            "reason code. Header `Idempotency-Key`: a repeat returns the same "
            "quote (200) and stores nothing again; without one a retry makes "
            "a new quote."
        ),
        request={
            "multipart/form-data": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["answer_papers", "assignment_files"],
                    },
                    "assignment_id": {"type": "string", "format": "uuid"},
                    "course_id": {"type": "string", "format": "uuid"},
                    "topic_id": {"type": "string", "format": "uuid", "nullable": True},
                    "files": {
                        "type": "array",
                        "items": {"type": "string", "format": "binary"},
                    },
                },
                "required": ["kind", "files"],
            }
        },
        parameters=[
            OpenApiParameter(
                "Idempotency-Key", str, OpenApiParameter.HEADER, required=False
            )
        ],
        responses={201: QUOTE_REPLY, 200: QUOTE_REPLY},
    )
    def post(self, request):
        refuse_oversize_request(request)
        body = CreateSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        data = body.validated_data
        relaunch: dict = {}
        assignment = course = None
        if data["kind"] == UploadQuote.Kind.ANSWER_PAPERS:
            assignment = get_object_or_404(
                Assignment.objects.filter(
                    teacher_course_access_q(request.user, prefix="course__")
                ),
                pk=data["assignment_id"],
            )
        else:
            course = get_object_or_404(
                reachable_courses(request.user), pk=data["course_id"]
            )
            if data.get("topic_id"):
                topic = get_object_or_404(Topic, pk=data["topic_id"], course=course)
                relaunch = {"topic_id": str(topic.pk)}
            else:
                relaunch = {"topic_id": None}
        key = (request.headers.get("Idempotency-Key") or "").strip()[:64] or None
        if key:
            existing = UploadQuote.objects.filter(
                teacher=request.user, idempotency_key=key
            ).first()
            if existing is not None:
                return Response(service.payload(existing), status=status.HTTP_200_OK)
        files = request.FILES.getlist("files")
        if not files:
            raise ParseError(NO_FILES)
        limit = settings.UPLOAD_QUOTE_MAX_FILES
        if len(files) > limit:
            raise ParseError(TOO_MANY_FILES.format(limit=limit))
        refused = nothing_spendable(request.user)
        if refused is not None:
            return refused
        service.check_room(request.user, sum(f.size or 0 for f in files))
        quote, created = service.create_quote(
            request.user,
            kind=data["kind"],
            assignment=assignment,
            course=course,
            relaunch=relaunch,
            key=key,
        )
        if not created:
            return Response(service.payload(quote), status=status.HTTP_200_OK)
        entries = service.add_files(quote, files)
        service.restart_clock(quote.pk)
        service.after_commit_measure([e for e in entries if not isinstance(e, tuple)])
        service.note_global_alert()
        return Response(
            service.create_reply(quote, entries), status=status.HTTP_201_CREATED
        )

    @extend_schema(
        summary="List my open upload quotes",
        parameters=[OpenApiParameter("open", bool, required=False)],
        responses=SUMMARY_REPLY,
    )
    def get(self, request):
        quotes = UploadQuote.objects.filter(
            teacher=request.user, state=UploadQuote.State.OPEN
        ).order_by("created_at")
        if request.query_params.get("open", "true").lower() not in ("true", "1"):
            quotes = UploadQuote.objects.filter(teacher=request.user).order_by(
                "created_at"
            )
        reachable = reachable_courses(request.user)
        rows = [
            service.summary(q)
            for q in quotes.select_related("assignment")
            if reachable.filter(
                pk=q.course_id or (q.assignment.course_id if q.assignment else None)
            ).exists()
        ]
        return Response(rows)


@extend_schema(tags=["Upload quotes"])
class UploadQuoteDetail(DarkUntilEnabled, APIView):
    permission_classes = [IsAuthenticated, IsTeacher]

    def get_throttles(self):
        return [UploadQuoteReadThrottle()]

    @extend_schema(summary="Read an upload quote", responses=QUOTE_REPLY)
    def get(self, request, pk):
        return Response(service.payload(own_quote(request, pk)))

    @extend_schema(
        summary="Cancel an upload quote",
        responses=inline_serializer(
            "UploadQuoteCancelled", {"state": serializers.CharField()}
        ),
    )
    def delete(self, request, pk):
        quote = service.cancel(own_quote(request, pk))
        return Response({"state": quote.state})


@extend_schema(tags=["Upload quotes"])
class UploadQuoteFiles(DarkUntilEnabled, APIView):
    permission_classes = [IsAuthenticated, IsTeacher]

    def get_throttles(self):
        return [UploadQuoteCreateThrottle()]

    @extend_schema(
        summary="Add files to an open upload quote",
        description=(
            "Multipart: `files`. Positions continue after the quote's last "
            "file; a file whose bytes the quote already holds is not stored "
            "again. Up to 100 files per quote and 500 MB per request. Open "
            "quotes only: QUOTE_CLOSED, or 410 QUOTE_EXPIRED."
        ),
        request={
            "multipart/form-data": {
                "type": "object",
                "properties": {
                    "files": {
                        "type": "array",
                        "items": {"type": "string", "format": "binary"},
                    }
                },
                "required": ["files"],
            }
        },
        responses={201: QUOTE_REPLY},
    )
    def post(self, request, pk):
        refuse_oversize_request(request)
        quote = own_quote(request, pk)
        files = request.FILES.getlist("files")
        if not files:
            raise ParseError(NO_FILES)
        refused = nothing_spendable(request.user)
        if refused is not None:
            return refused
        try:
            entries = service.add_to_quote(quote, files)
        except service.TooManyFiles:
            raise ParseError(
                TOO_MANY_FILES.format(limit=settings.UPLOAD_QUOTE_MAX_FILES)
            ) from None
        quote.refresh_from_db()
        return Response(
            service.create_reply(quote, entries), status=status.HTTP_201_CREATED
        )


@extend_schema(tags=["Upload quotes"])
class UploadQuoteConfirm(DarkUntilEnabled, APIView):
    permission_classes = [IsAuthenticated, IsTeacher]

    def get_throttles(self):
        return [UploadQuoteCreateThrottle()]

    @extend_schema(
        summary="Start the upload: confirm a ready quote",
        description=(
            "Runs every file that fits the balance now and parks the rest, "
            "kept and retryable (they never need uploading again). The body "
            "is the batch routes' own (`session_id`, `tasks`) plus "
            "`quote_id`: 202 the first time, 200 with the same body when "
            "repeated. Optional `would_park` is accepted and ignored. 409 "
            "QUOTE_NOT_READY, QUOTE_CLOSED; 410 QUOTE_EXPIRED."
        ),
        request=inline_serializer(
            "UploadQuoteConfirmBody",
            {
                "would_park": serializers.ListField(
                    child=serializers.IntegerField(), required=False
                )
            },
        ),
        responses={202: CONFIRM_REPLY, 200: CONFIRM_REPLY},
    )
    def post(self, request, pk):
        quote = own_quote(request, pk)
        body, replayed = confirm_quote(quote.pk, request.user, request)
        return Response(
            body, status=status.HTTP_200_OK if replayed else status.HTTP_202_ACCEPTED
        )
