from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from billing.models import (
    PlanFeature,
    PlanFeatureInclusion,
    PlanFeatureKey,
    PlanType,
    SubscriptionPlan,
)

FEATURE_CATALOGUE = {
    PlanFeatureKey.UNLIMITED_COURSES: False,
    PlanFeatureKey.INVITE_STUDENTS_UPLOAD: False,
    PlanFeatureKey.BATCH_GRADING: False,
    PlanFeatureKey.BASIC_INSIGHTS: False,
    PlanFeatureKey.ADVANCED_ASSIGNMENT_ANALYTICS: False,
    PlanFeatureKey.ADVANCED_STUDENT_ANALYTICS: False,
    PlanFeatureKey.ADVANCED_COURSE_ANALYTICS: False,
    PlanFeatureKey.AI_PROMPT_ANALYTICS_SUMMARY: True,
    PlanFeatureKey.AI_PROMPT_ASSIGNMENT_CREATION: True,
    PlanFeatureKey.CREDIT_ROLLOVER_25: False,
    PlanFeatureKey.PRE_SCHEDULED_GRADING: False,
    PlanFeatureKey.AI_EMAIL_FEEDBACK: False,
    PlanFeatureKey.ADMIN_MANAGED_BILLING: False,
    PlanFeatureKey.SHARED_CREDIT_POOL: False,
    PlanFeatureKey.DEDICATED_SUPPORT: False,
}


# ---------------------------------------------------------------------------
# 2. Per-tier feature sets, straight from the Subscription Model image.
#    Each list is ONLY the features that tier includes - "everything in
#    Standard, plus X" bullets are expanded out explicitly rather than
#    left implicit, so PLAN_FEATURE_SETS below is always the complete,
#    literal set for that plan (nothing is inherited silently).
# ---------------------------------------------------------------------------

_BASELINE = [
    PlanFeatureKey.UNLIMITED_COURSES,
    PlanFeatureKey.INVITE_STUDENTS_UPLOAD,
    PlanFeatureKey.BATCH_GRADING,
    PlanFeatureKey.BASIC_INSIGHTS,
    PlanFeatureKey.ADVANCED_ASSIGNMENT_ANALYTICS,
    PlanFeatureKey.ADVANCED_STUDENT_ANALYTICS,
]


_PRO_ADDITIONS = _BASELINE + [
    PlanFeatureKey.AI_PROMPT_ANALYTICS_SUMMARY,
    PlanFeatureKey.AI_PROMPT_ASSIGNMENT_CREATION,
    PlanFeatureKey.ADVANCED_COURSE_ANALYTICS,
    PlanFeatureKey.CREDIT_ROLLOVER_25,
]


_POWER_ADDITIONS = _PRO_ADDITIONS + [
    PlanFeatureKey.PRE_SCHEDULED_GRADING,
    PlanFeatureKey.AI_EMAIL_FEEDBACK,
]


_ALL_FEATURES = list(FEATURE_CATALOGUE.keys())  # trial / beta: everything unlocked

PLAN_FEATURE_SETS = {
    # --- Individual ---
    PlanType.TRIAL: _ALL_FEATURES,  # image: "Free Trial ... All Features Accessible"
    PlanType.STANDARD: _BASELINE,
    PlanType.STANDARD_ANNUAL: _BASELINE,
    PlanType.PRO: _PRO_ADDITIONS,
    PlanType.PRO_ANNUAL: _PRO_ADDITIONS,
    PlanType.POWER: _POWER_ADDITIONS,
    PlanType.POWER_ANNUAL: _POWER_ADDITIONS,
    PlanType.BETA: _ALL_FEATURES,  # internal/testing tier
    PlanType.CUSTOM: _POWER_ADDITIONS,  # individual contact-sales - assumption, review
    # --- License --- (Standard tier is not offered under License at all -
    # already enforced separately in LicenseSubscriptionService.validate_license_plan)
    PlanType.PRO_LICENSE: _PRO_ADDITIONS + [PlanFeatureKey.ADMIN_MANAGED_BILLING],
    PlanType.POWER_LICENSE: _POWER_ADDITIONS + [PlanFeatureKey.ADMIN_MANAGED_BILLING],
    # Custom license tiers are explicitly "case-by-case" per the image -
    # these are reasonable defaults only. Adjust per actual contract, or
    # remove from this dict entirely and manage those plans purely via
    # the admin if every contract genuinely differs.
    PlanType.CUSTOM_LICENSE_STARTER: _PRO_ADDITIONS
    + [PlanFeatureKey.ADMIN_MANAGED_BILLING],
    PlanType.CUSTOM_LICENSE_MID: _POWER_ADDITIONS
    + [PlanFeatureKey.ADMIN_MANAGED_BILLING],
    PlanType.CUSTOM_LICENSE_HIGH: _POWER_ADDITIONS
    + [
        PlanFeatureKey.ADMIN_MANAGED_BILLING,
        PlanFeatureKey.SHARED_CREDIT_POOL,
        PlanFeatureKey.DEDICATED_SUPPORT,
    ],
}


# ---------------------------------------------------------------------------
# 3. Rollover percentages, straight from the image's "Credit Rollover
#    Policy" note (15% Standard, 25% Pro/Power). This is the field that
#    actually drives rollover math in SubscriptionService/
#    LicenseSubscriptionService - the CREDIT_ROLLOVER_25 PlanFeature above
#    is just a pricing-page badge for the 25% tier; it has no bearing on
#    the real calculation. For any plan NOT listed here (Standard's 15% has
#    no corresponding badge key, and CUSTOM/CUSTOM_LICENSE_* are
#    deliberately omitted since those are negotiated per-contract), set
#    carry_over_percent directly via the admin instead of this command.
# ---------------------------------------------------------------------------
ROLLOVER_PERCENTAGES = {
    PlanType.TRIAL: 0,  # trial credits don't roll over - there is no next cycle
    PlanType.STANDARD: 15,
    PlanType.STANDARD_ANNUAL: 15,
    PlanType.PRO: 25,
    PlanType.PRO_ANNUAL: 25,
    PlanType.POWER: 25,
    PlanType.POWER_ANNUAL: 25,
    PlanType.PRO_LICENSE: 25,
    PlanType.POWER_LICENSE: 25,
}


class Command(BaseCommand):
    help = (
        "Seeds PlanFeature/PlanFeatureInclusion rows and syncs "
        "carry_over_percent from the Grade A+ Subscription Model. "
        "Idempotent - safe to re-run after editing the mapping tables "
        "at the top of this file. Not run automatically - if this is "
        "never run in an environment, billing/checks.py's "
        "check_plan_feature_catalogue_seeded system check will surface a "
        "warning naming the missing gating key(s) on every "
        "`manage.py check`/`migrate`/`runserver`."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would change without writing to the database.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        with transaction.atomic():
            self._seed_feature_catalogue(dry_run)
            self._seed_plan_inclusions(dry_run)
            self._sync_rollover_percentages(dry_run)

            if dry_run:
                # Nothing above actually wrote anything in dry-run mode,
                # but force a rollback anyway as a safety net in case this
                # file is edited later and a write slips into a dry-run path.
                transaction.set_rollback(True)
                self.stdout.write(
                    self.style.WARNING("Dry run complete - no changes were made.")
                )

    def _seed_feature_catalogue(self, dry_run):
        labels = dict(PlanFeatureKey.choices)  # key -> human label

        self.stdout.write(self.style.MIGRATE_HEADING("PlanFeature catalogue"))

        for key, is_gating in FEATURE_CATALOGUE.items():
            if dry_run:
                exists = PlanFeature.objects.filter(pk=key).exists()
                self.stdout.write(
                    f"  [dry-run] {key}: "
                    f"{'would update' if exists else 'would create'} "
                    f"(is_gating_feature={is_gating})"
                )
                continue

            obj, created = PlanFeature.objects.update_or_create(
                key=key,
                defaults={
                    "label": labels[key],
                    "is_gating_feature": is_gating,
                },
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"  {'Created' if created else 'Updated'} {key} "
                    f"(is_gating_feature={is_gating})"
                )
            )

    def _seed_plan_inclusions(self, dry_run):
        self.stdout.write(self.style.MIGRATE_HEADING("PlanFeatureInclusion per plan"))

        for plan_name, feature_keys in PLAN_FEATURE_SETS.items():
            plan = SubscriptionPlan.objects.filter(name=plan_name).first()
            if not plan:
                self.stdout.write(
                    self.style.WARNING(
                        f"  SubscriptionPlan {plan_name!r} not found - skipping. "
                        f"Create it first, then re-run this command."
                    )
                )
                continue

            feature_set = set(feature_keys)
            summary = []

            for order, key in enumerate(FEATURE_CATALOGUE.keys()):
                included = key in feature_set
                summary.append(f"{key}={'Y' if included else 'n'}")

                if dry_run:
                    continue

                feature = PlanFeature.objects.get(pk=key)
                PlanFeatureInclusion.objects.update_or_create(
                    plan=plan,
                    feature=feature,
                    defaults={
                        "included": included,
                        "display_order": order,
                    },
                )

            prefix = "[dry-run] " if dry_run else ""
            self.stdout.write(
                self.style.SUCCESS(f"  {prefix}{plan_name}: " + ", ".join(summary))
            )

    def _sync_rollover_percentages(self, dry_run):
        self.stdout.write(self.style.MIGRATE_HEADING("carry_over_percent sync"))

        for plan_name, percent in ROLLOVER_PERCENTAGES.items():
            plan = SubscriptionPlan.objects.filter(name=plan_name).first()
            if not plan:
                self.stdout.write(
                    self.style.WARNING(
                        f"  SubscriptionPlan {plan_name!r} not found - skipping."
                    )
                )
                continue

            if dry_run:
                self.stdout.write(
                    f"  [dry-run] {plan_name}: carry_over_percent "
                    f"{plan.carry_over_percent} -> {percent}"
                )
                continue

            target = Decimal(str(percent))
            if plan.carry_over_percent == target:
                self.stdout.write(f"  {plan_name}: already {percent}%, no change")
                continue

            plan.carry_over_percent = target
            plan.save(update_fields=["carry_over_percent"])
            self.stdout.write(
                self.style.SUCCESS(f"  {plan_name}: carry_over_percent -> {percent}%")
            )
