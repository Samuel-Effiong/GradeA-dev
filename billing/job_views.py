"""GET billing/jobs/<id> (Epic B1b-7; plan 08 §3.10, §7 item 6; 08b §1.5;
NOTES §7.2 D7-c, contract §7.10).

Read-only: the job's estimate, actual cost, counts and state. It returns
codes and numbers only; the app shows the approved texts (#15) from them.

Who may read a job:
- its owner, the user of the job's wallet;
- a school admin whose school is the school of a license that holds an
  active SchoolCreditAllocation for that user;
- the super admin (is_superuser and SUPER_ADMIN).
Anyone else gets 404, so a job's existence is not revealed.
"""

from django.db.models import Q
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.generics import RetrieveAPIView
from rest_framework.permissions import IsAuthenticated

from users.models import UserTypes

from .models import AIJob


class AIJobDetailSerializer(serializers.ModelSerializer):
    state = serializers.CharField(source="status")
    estimate = serializers.SerializerMethodField()
    verdict = serializers.CharField(source="sufficiency_verdict", allow_null=True)
    actual = serializers.SerializerMethodField()
    items = serializers.SerializerMethodField()

    class Meta:
        model = AIJob
        fields = [
            "id",
            "job_type",
            "state",
            "estimate",
            "verdict",
            "actual",
            "items",
            "stopped_at_item",
            "queued_at",
            "expires_at",
            "created_at",
        ]
        read_only_fields = fields

    def get_estimate(self, job) -> dict:
        return {
            "p50": job.estimate_p50,
            "p90": job.estimate_p90,
            "source": job.estimate_source,
        }

    def get_actual(self, job) -> dict:
        return {
            "consumed_credits": job.consumed_credits,
            "unbilled_credits": job.unbilled_credits,
        }

    def get_items(self, job) -> dict:
        return {
            "total": job.total_items,
            "completed": job.completed_items,
            "failed": job.failed_items,
            "left": job.total_items - job.completed_items - job.failed_items,
        }


def visible_jobs(user):
    """The jobs `user` may read (module docstring)."""
    jobs = AIJob.objects.all()
    if user.is_superuser and user.user_type == UserTypes.SUPER_ADMIN:
        return jobs
    scope = Q(wallet__user=user)
    if user.user_type == UserTypes.SCHOOL_ADMIN and user.school_id is not None:
        scope |= Q(
            wallet__user__school_credit_allocations__is_active=True,
            wallet__user__school_credit_allocations__license_subscription__school_id=(
                user.school_id
            ),
        )
    return jobs.filter(scope).distinct()


@extend_schema(
    tags=["Billing"],
    summary="Retrieve an AI job",
    description="An AI job's estimate, actual cost, item counts and state, "
    "for its owner, a school admin of the owner's license, or the super "
    "admin. Anyone else gets 404.",
    responses=AIJobDetailSerializer,
)
class AIJobDetailView(RetrieveAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = AIJobDetailSerializer

    def get_queryset(self):
        return visible_jobs(self.request.user)
