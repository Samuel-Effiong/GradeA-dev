"""Cache-generation bumps for dashboard-owned models.

`dashboard` had no signals module before H-1 stage 2. It needs one because
`SchoolAtRiskSnapshot` is a dependency of the at-risk trend chart
(`schooladmins:*:view__at_risk_trend`, family 24) and is written by the
daily at-risk task rather than by any request path - so nothing in the
request-driven receivers can invalidate it.

Written as a receiver rather than as a bump inside `dashboard/tasks.py` on
purpose: the task is the only writer TODAY, and a receiver keeps that from
being load-bearing. A backfill, a management command or an admin edit would
otherwise write snapshots that never invalidate the chart.
"""

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from AutoGrader.cache_generation import SCOPE_SCHOOL, bump_many
from dashboard.models import SchoolAtRiskSnapshot


@receiver([post_save, post_delete], sender=SchoolAtRiskSnapshot)
def bump_school_on_at_risk_snapshot(sender, instance, **kwargs):
    """A new daily snapshot changes the school's at-risk trend chart.

    Only the school generation moves: the chart is school-scoped, and the
    snapshot says nothing about any individual user.
    """
    bump_many([(SCOPE_SCHOOL, instance.school_id)])
