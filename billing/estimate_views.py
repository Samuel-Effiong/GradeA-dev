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


class _SubmissionIds(serializers.Field):
    def to_internal_value(self, data):
        if data == ALL_UNGRADED:
            return ALL_UNGRADED
        return serializers.ListField(child=serializers.UUIDField()).run_validation(data)


class EstimateRequestSerializer(serializers.Serializer):
    assignment_id = serializers.UUIDField()
    submission_ids = _SubmissionIds()


class BillingEstimateView(APIView):
    permission_classes = [IsAuthenticated, IsTeacher]

    def post(self, request):
        body = EstimateRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
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
