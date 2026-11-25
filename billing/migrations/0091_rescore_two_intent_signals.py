"""Step 2 (owner, 2026-11-25): rescore every BetaProfile once under the two
signals, so existing profiles show the new score without waiting for the
nightly task.

The score is 50 for credits used >= 60% of the allocation (no allocation:
nothing) plus 50 for last_active_at >= now - 7 days. A frozen copy of
billing.services.AnalyticsService.conversion_score's rule (a migration must
not import app code that can change later). usage_velocity is unchanged.
Reversing does nothing: the old scores are not kept.
"""

from datetime import timedelta

from django.db import migrations
from django.utils import timezone

BATCH = 500


def score(profile, now):
    points = 0
    allocation = profile.initial_beta_credits or 0
    if allocation > 0 and (profile.total_credits_used or 0) * 10 >= allocation * 6:
        points += 50
    if (
        profile.last_active_at is not None
        and profile.last_active_at >= now - timedelta(days=7)
    ):
        points += 50
    return float(points)


def rescore(apps, schema_editor):
    BetaProfile = apps.get_model("billing", "BetaProfile")
    now = timezone.now()
    changed = []
    for profile in BetaProfile.objects.only(
        "pk",
        "initial_beta_credits",
        "total_credits_used",
        "last_active_at",
        "conversion_probability",
    ).iterator(chunk_size=BATCH):
        new = score(profile, now)
        if profile.conversion_probability != new:
            profile.conversion_probability = new
            changed.append(profile)
        if len(changed) >= BATCH:
            BetaProfile.objects.bulk_update(changed, ["conversion_probability"])
            changed = []
    if changed:
        BetaProfile.objects.bulk_update(changed, ["conversion_probability"])


class Migration(migrations.Migration):
    dependencies = [
        ("billing", "0090_plain_credits_not_added_description"),
    ]

    operations = [
        migrations.RunPython(rescore, migrations.RunPython.noop),
    ]
