"""POST billing/estimate (Epic B1b-4; plan 08 §3.6; SM rulings Q5, Q6, D-b).

Advisory only: it returns the estimate's codes and numbers, writes nothing
and holds nothing, and no route refuses a teacher because of its verdict.

Who may ask: a teacher, for an assignment of a course they can reach. That
is the same rule grade-all applies (AssignmentViewSet.get_queryset filters on
classrooms.models.teacher_course_access_q, the same function), so another
teacher's assignment gets grade-all's 404. It deliberately does not use
HasCreditBalance: that refuses an empty wallet with 402, and a teacher at 0
must get the verdict INSUFFICIENT (FR-B-04's 0 boundary).
"""

from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from assignments.models import Assignment
from billing import estimates
from classrooms.models import teacher_course_access_q
from classrooms.permissions import IsTeacher

ALL_UNGRADED = "all_ungraded"
# B1b-6b (H-1, owner 2026-11-11): a listed body holds at most 500 ids;
# "all_ungraded" has no limit.
LIST_LIMIT = 500
# Owner-approved (team/approved_wording_b1b_2026-11-09.md, H-1, amended
# 2026-11-11): the one sentence a teacher sees over the limit.
OVER_LIMIT_MESSAGE = (
    "You can get a cost estimate for up to 500 papers at a time. "
    "Pick fewer papers, or choose all ungraded papers."
)


class _SubmissionIds(serializers.Field):
    def to_internal_value(self, data):
        if data == ALL_UNGRADED:
            return ALL_UNGRADED
        if isinstance(data, list) and len(data) > LIST_LIMIT:
            # DRF's own ListField text, under a code the error renderer does
            # not reword as a character limit (code max_length would read
            # "Use 500 characters or fewer").
            raise serializers.ValidationError(
                serializers.ListField.default_error_messages["max_length"].format(
                    max_length=LIST_LIMIT
                ),
                code="max_items",
            )
        return serializers.ListField(child=serializers.UUIDField()).run_validation(data)


def _shown_errors(errors):
    """Over the limit, the body is a structured refusal (a string `code`
    and `message`), which the error renderer shows as exactly `message`;
    field_errors keeps every field's own text. Otherwise, unchanged."""
    if any(
        getattr(error, "code", None) == "max_items"
        for error in errors.get("submission_ids", [])
    ):
        return {"code": "max_items", "message": OVER_LIMIT_MESSAGE, **errors}
    return errors


class EstimateRequestSerializer(serializers.Serializer):
    assignment_id = serializers.UUIDField()
    submission_ids = _SubmissionIds()


class BillingEstimateView(APIView):
    permission_classes = [IsAuthenticated, IsTeacher]

    def post(self, request):
        body = EstimateRequestSerializer(data=request.data)
        if not body.is_valid():
            raise serializers.ValidationError(_shown_errors(body.errors))
        assignment = get_object_or_404(
            Assignment.objects.filter(
                teacher_course_access_q(request.user, prefix="course__")
            ),
            pk=body.validated_data["assignment_id"],
        )
        chosen = body.validated_data["submission_ids"]
        if chosen == ALL_UNGRADED:
            submissions = assignment.submissions.filter(graded_at__isnull=True)
        else:
            submissions = assignment.submissions.filter(pk__in=chosen)
        n = submissions.count()
        representative = submissions.order_by("pk").first()
        return Response(
            estimates.advise(request.user, assignment, n, representative=representative)
        )
