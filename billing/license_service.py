"""
LicenseSubscriptionService

Handles all business logic for institutional License subscriptions.

A License subscription is a school-level subscription that covers multiple teachers.
Each teacher gets an individual credit allocation from their school's license,
but cannot modify billing settings themselves. The school admin manages the license.

Key principles:
- One License per school
- Each teacher gets individual CreditWallet (NOT shared)
- Each teacher gets SchoolCreditAllocation (tracks their monthly allocation)
- Teachers are independent (one's exhausted credits don't affect others)
- All logic isolated from SubscriptionService (for Individual subscriptions)
"""

import logging
from datetime import timedelta
from typing import Any, Dict, List, Optional

from dateutil.relativedelta import relativedelta  # type: ignore
from django.conf import settings
from django.core.cache import cache
from django.db import models, transaction
from django.db.models import F
from django.template.loader import render_to_string

# from django.db.models import F, Q
from django.utils import timezone

from AutoGrader.error_messages import describe_stripe_error, describe_user_error
from AutoGrader.tasks import send_email_task
from classrooms.models import School
from users.mailerlite_service import queue_sync
from users.models import CustomUser, RegistrationMethod, UserTypes
from users.services import otp_manager
from users.utils import is_business_email, is_exempt_email_domain

from .billing_transaction_service import BillingTransactionService
from .context import clear_license_invitation_context, set_license_invitation_context
from .imports import stripe
from .models import (  # CONVERSION_FACTOR,; UserSubscription,
    CONVERSION_FACTOR,
    BillingTransactionMethod,
    BillingTransactionSource,
    BillingTransactionStatus,
    BillingTransactionType,
    CreditBucket,
    CreditBucketType,
    CreditLedger,
    CreditLedgerType,
    CreditWallet,
    LicenseBillingMethod,
    LicenseBillingRecord,
    LicenseBillingRecordType,
    LicenseOverageOfflineRequest,
    LicenseOverageOfflineRequestStatus,
    LicenseOveragePurchaseIntent,
    LicenseOveragePurchaseStatus,
    LicenseSubscription,
    PlanCategory,
    PlanTier,
    SchoolCreditAllocation,
    StripeSubscriptionStatus,
    SubscriptionPlan,
)

logger = logging.getLogger(__name__)

_DEFAULT_LICENSE_BILLING_METHOD: str = str(LicenseBillingMethod.STRIPE)


def sync_teachers_under_license_to_mailerlite(license_sub: LicenseSubscription) -> None:
    """
    Re-syncs every teacher still allocated under `license_sub` to MailerLite.

    Call this after flipping a LicenseSubscription's is_active flag, or
    changing its plan - either one alters what get_active_subscription()
    and subscription_tier return for every teacher under it, so their
    MailerLite fields go stale unless re-synced explicitly.

    user__is_active=True excludes teachers who were invited but haven't
    completed activation yet - same rule queue_sync() applies for a
    single user (see users/mailerlite_service.py).
    """
    from users.tasks import sync_user_to_mailerlite

    teacher_ids = license_sub.allocations.filter(
        is_active=True, user__is_active=True
    ).values_list("user_id", flat=True)
    for user_id in teacher_ids:
        sync_user_to_mailerlite.delay(str(user_id))


class IndividualSubscriptionConflictError(Exception):
    """Raised when a teacher has an active individual subscription and cannot
    be added to a license
    """

    pass


class LicenseSubscriptionService:
    """
    Service for managing institutional License subscriptions.

    All operations are atomic and include comprehensive audit logging.
    """

    # Fixed monthly AI-credit allowance granted to a license's admin_user
    # (school admin), separate from teacher allocations. School admins
    # cannot grade/perform AI tasks themselves, but their dashboard uses AI
    # to generate analytics, which requires credits the same way any other
    # AI feature does.

    ADMIN_ANALYTICS_CREDITS_DISPLAY = 5_000
    ADMIN_ANALYTICS_CREDITS_RAW = 5_000 * CONVERSION_FACTOR

    _OVERAGE_LOCK_TIMEOUT_SECONDS = 30
    MAX_BLOCKS_PER_PURCHASE = 1000  # Defensive cap against fat-finger/typo purchase

    @staticmethod
    def _is_superadmin_actor(user) -> bool:
        return bool(user.is_superuser and user.user_type == UserTypes.SUPER_ADMIN)

    @staticmethod
    def _is_license_school_admin_actor(license_sub: LicenseSubscription, user) -> bool:
        return bool(license_sub.admin_user_id == user.id)

    @staticmethod
    def _overage_eligible_allocations_q(license_sub: LicenseSubscription):
        """
        Q object selecting SchoolCreditAllocation rows eligible to receive
        overage blocks under this license: every regular teacher
        allocation, PLUS the license's own admin_user's analytics
        allocation (is_admin_allocation=True) — the admin can buy overage
        for their own analytics allocation same as for any teacher. No
        OTHER admin-flagged allocation ever qualifies, since a license has
        exactly one admin_user.

        Shared by every place that validates or grants overage
        (_validate_overage_purchase_request, _grant_overage_offline, the
        Stripe checkout-completed webhook, and
        approve_overage_offline_request) so the eligibility rule can't
        drift between them.
        """
        return models.Q(is_admin_allocation=False) | models.Q(
            is_admin_allocation=True, user_id=license_sub.admin_user_id
        )

    @staticmethod
    def _validate_overage_purchase_request(
        license_sub: LicenseSubscription, allocations: dict, total_blocks: int
    ) -> None:
        """
        Shared, read-only validation used by both branches BEFORE any lock
        is taken or any Stripe call is made. Raises ValueError on any
        problem. Does not check per-teacher activity under lock — callers
        that mutate state must re-validate that under select_for_update()
        themselves, since a teacher's status can change between this check
        and the actual grant.
        """
        if not license_sub.is_active:
            raise ValueError("License is not active.")

        if total_blocks <= 0:
            raise ValueError("total_blocks must be a positive integer.")

        if total_blocks > LicenseSubscriptionService.MAX_BLOCKS_PER_PURCHASE:
            raise ValueError(
                f"total_blocks ({total_blocks}) exceeds the maximum of "
                f"{LicenseSubscriptionService.MAX_BLOCKS_PER_PURCHASE} blocks "
                f"per purchase. Split this into multiple purchases if needed."
            )

        if not allocations:
            raise ValueError("allocations cannot be empty.")

        for teacher_id, blocks in allocations.items():
            if not isinstance(blocks, int) or blocks <= 0:
                raise ValueError(
                    f"Invalid block count for teacher {teacher_id}: {blocks!r}."
                )

        allocated_sum = sum(allocations.values())
        if allocated_sum != total_blocks:
            raise ValueError(
                f"Sum of allocated blocks ({allocated_sum}) must equal "
                f"total_blocks ({total_blocks})."
            )

        plan = license_sub.plan
        if not plan.overage_block_price or plan.overage_block_price <= 0:
            raise ValueError("This plan has no overage pricing configured.")
        if not plan.overage_block_size or plan.overage_block_size <= 0:
            raise ValueError("This plan has no overage block size configured.")

        teacher_ids = list(allocations.keys())
        active_ids = {
            str(uid)
            for uid in SchoolCreditAllocation.objects.filter(
                LicenseSubscriptionService._overage_eligible_allocations_q(license_sub),
                license_subscription=license_sub,
                user_id__in=teacher_ids,
                is_active=True,
            ).values_list("user_id", flat=True)
        }
        missing = {str(t) for t in teacher_ids} - active_ids
        if missing:
            raise ValueError(
                f"Teachers not active under this license: {', '.join(sorted(missing))}"
            )

    @staticmethod
    def _resolve_effective_price(
        license_sub, new_plan, custom_price_cents=None, remove_custom_price=False
    ):
        old_effective_price = (
            license_sub.custom_price_cents or license_sub.plan.price_cents
        )

        if remove_custom_price:
            # Remove custom price; use plan default
            new_effective_price = new_plan.price_cents
            new_custom_price_cents = None
        elif custom_price_cents is not None:
            # Use provided custom price
            new_effective_price = custom_price_cents
            new_custom_price_cents = custom_price_cents
        else:
            # Keep existing custom setting (if any) or use plan default
            if license_sub.custom_price_cents is not None:
                new_effective_price = license_sub.custom_price_cents
                new_custom_price_cents = license_sub.custom_price_cents
            else:
                new_effective_price = new_plan.price_cents
                new_custom_price_cents = None

        return old_effective_price, new_effective_price, new_custom_price_cents

    @staticmethod
    def validate_license_plan(plan: SubscriptionPlan) -> None:
        """
        Validates that a plan is suitable for License subscriptions.

        Args:
            plan: SubscriptionPlan to validate

        Raises:
            ValueError: If plan is not configured for LICENSE category
        """
        if plan.category != PlanCategory.LICENSE:
            raise ValueError(
                f"Plan {plan.name} has category={plan.category}, "
                f"but only LICENSE plans are allowed for license subscriptions."
            )

        if plan.monthly_credits is None or plan.monthly_credits == 0:
            raise ValueError(
                f"License plan {plan.name} must define monthly_credits. "
                f"Custom/contact-sales plans cannot be activated directly."
            )

        if plan.tier == PlanTier.STANDARD:
            raise ValueError(
                "Standard Grader tier is not available under License subscription"
            )

    @staticmethod
    def validate_admin_user(admin_user: CustomUser, school: School) -> None:
        """
        Validates that admin_user is authorized to manage licenses for the school.

        Args:
            admin_user: User attempting to manage the license
            school: School the license belongs to

        Raises:
            ValueError: If admin_user is not authorized
        """
        # Check if user is school admin and belongs to the school
        if admin_user.user_type == "STUDENT":
            raise ValueError("Student users cannot manage license subscriptions.")

        # Optionally check if admin_user is associated with the school
        if admin_user.school and admin_user.school != school:
            raise ValueError(
                f"User {admin_user.email} is not authorized to manage "
                f"licenses for school {school.name}."
            )

    @staticmethod
    def _rollover_and_grant_monthly_bucket(
        teacher: CustomUser,
        wallet: CreditWallet,
        plan: SubscriptionPlan,
        grant_amount: int,
        new_expiry,
        now,
        reference: str,
        metadata: dict,
    ) -> CreditBucket:
        """
        Expires whichever MONTHLY bucket is CURRENTLY ACTIVE for the teacher
        (rolling over its unused balance per `plan`'s carry_over rules), then
        creates a fresh MONTHLY bucket with `grant_amount` credits expiring
        at `new_expiry`.

        Deliberately looks up the bucket by "currently active"
        (expires_at IS NULL OR expires_at > now) rather than "already
        expired" (expires_at <= now). The latter is only safe when the
        caller is guaranteed to run AFTER the natural cycle end (true for
        the Stripe/Celery renewal path) — it is NOT safe for a superadmin
        renewing an offline license EARLY, where the current bucket is
        still live. Using the expired-only filter there would silently
        skip rollover and leave the teacher holding both the old live
        bucket and a new one simultaneously (a double-grant). This version
        is safe for both callers.
        """
        current_bucket = (
            wallet.buckets.select_for_update()
            .filter(bucket_type=CreditBucketType.MONTHLY, is_processed=False)
            .order_by("-created_at")
            .first()
        )

        if current_bucket:
            unused = max(0, current_bucket.total_credits - current_bucket.used_credits)
            if unused > 0:
                rollover_amount, cap_meta = wallet.compute_capped_rollover(
                    plan, unused, monthly_amount=grant_amount, now=now
                )
                if rollover_amount > 0:
                    expiry = now + relativedelta(
                        months=1 * plan.carry_over_expiry_months
                    )
                    carry_bucket = CreditBucket.objects.create(
                        wallet=wallet,
                        bucket_type=CreditBucketType.CARRY_OVER,
                        total_credits=rollover_amount,
                        used_credits=0,
                        expires_at=expiry,
                    )
                    CreditLedger.objects.create(
                        user=teacher,
                        bucket=carry_bucket,
                        ledger_type=CreditLedgerType.GRANT,
                        amount=rollover_amount,
                        reference=f"Rollover — {reference}",
                        metadata={**metadata, "previous_unused": unused, **cap_meta},
                    )
                elif cap_meta["requested_rollover"] > 0:
                    logger.info(
                        "License rollover fully suppressed by max_bank for "
                        "teacher %s: requested %d (%s).",
                        teacher.email,
                        cap_meta["requested_rollover"],
                        cap_meta,
                    )

            current_bucket.expires_at = now
            current_bucket.is_processed = True
            current_bucket.save(
                update_fields=["expires_at", "is_processed", "updated_at"]
            )

        new_bucket = CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=grant_amount,
            used_credits=0,
            expires_at=new_expiry,
        )
        CreditLedger.objects.create(
            user=teacher,
            bucket=new_bucket,
            ledger_type=CreditLedgerType.GRANT,
            amount=grant_amount,
            reference=reference,
            metadata=metadata,
        )
        return new_bucket

    @staticmethod
    def _grant_admin_allocation(
        license_sub: LicenseSubscription,
    ) -> SchoolCreditAllocation:
        """
        Grants the license's admin_user their own fixed, recurring
        analytics-only credit allocation, so their dashboard's AI-generated
        analytics have credits to draw from — school admins can't grade or
        run other AI teacher features, but the dashboard shown to them
        still calls AI to build those analytics.

        Reuses SchoolCreditAllocation/CreditBucket/CreditLedger exactly as
        teacher allocations do, which is what makes this allocation "just
        work" with the existing monthly-refresh task
        (process_license_monthly_credit_refreshes), contract renewal
        (process_license_renewal / process_offline_renewal — both iterate
        every active allocation generically), and the overage-purchase
        endpoints (purchase_teacher_overage / grant_manual_teacher_overage
        both validate beneficiaries purely via an active
        SchoolCreditAllocation row under the license) with no further code
        changes needed in any of those paths.

        Marked is_admin_allocation=True so it is excluded from:
          - LicenseSubscription.teacher_count / seats_remaining
          - active_teacher_count wherever it's computed (license_views.py,
            serializers.py)
          - the monthly_allocation overwrite in change_license_plan /
            update_license_plan (this allowance is fixed, not tied to plan)
          - LicenseSubscription.total_credits_consumed increments (see
            billing/signals.py: update_license_consumption)

        Idempotent: safe to call multiple times for the same license (e.g.
        to backfill an existing license created before this feature
        existed) — reuses get_or_create on the (license_subscription, user)
        unique-together pair and only ever creates one CreditBucket/GRANT
        per allocation. Deliberately NOT called from
        process_license_renewal / process_offline_renewal: those methods
        already iterate every currently-active allocation and would grant
        this SAME allocation a second time in the same renewal cycle if
        this method both created it AND the iteration below picked it up —
        backfilling a pre-existing license is therefore a separate,
        explicit action (e.g. a one-off management command), not something
        that happens implicitly as a side effect of a renewal.

        Args:
            license_sub: The license subscription whose admin should be
                granted the allowance. Uses license_sub.admin_user.

        Returns:
            SchoolCreditAllocation: The admin's allocation (existing or
                newly created).
        """

        admin_user = license_sub.admin_user
        now = timezone.now()
        next_refresh = now + relativedelta(months=1)
        raw_amount = LicenseSubscriptionService.ADMIN_ANALYTICS_CREDITS_RAW

        # Ensure the admin has a CreditWallet (should already exist via
        # users/signals.py on account creation, but defensive).
        wallet, _ = CreditWallet.objects.get_or_create(user=admin_user)

        allocation, created = SchoolCreditAllocation.objects.get_or_create(
            license_subscription=license_sub,
            user=admin_user,
            defaults={
                "monthly_allocation": raw_amount,
                "is_active": True,
                "is_admin_allocation": True,
                "next_credit_grant_at": next_refresh,
            },
        )

        if not created:
            if allocation.is_active:
                # Already active — nothing to do. Do NOT create another
                # bucket/ledger entry here; the normal monthly-refresh /
                # renewal machinery is what grants subsequent cycles.
                logger.debug(
                    "Admin allocation for license %s (admin %s) already "
                    "active — skipping duplicate grant.",
                    license_sub.id,
                    admin_user.email,
                )
                return allocation

            # Reactivating a previously deactivated admin allocation (e.g.
            # the license was cancelled and is being reinstated).
            allocation.is_active = True
            allocation.is_admin_allocation = True
            allocation.monthly_allocation = raw_amount
            allocation.next_credit_grant_at = next_refresh
            allocation.save(
                update_fields=[
                    "is_active",
                    "is_admin_allocation",
                    "monthly_allocation",
                    "next_credit_grant_at",
                    "updated_at",
                ]
            )

            logger.info(
                "Reactivated admin analytics allocation for license %s (admin %s)",
                license_sub.id,
                admin_user.email,
            )

        # Create the initial MONTHLY bucket for the admin's allowance
        monthly_bucket = CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=raw_amount,
            used_credits=0,
            expires_at=next_refresh,
        )

        CreditLedger.objects.create(
            user=admin_user,
            bucket=monthly_bucket,
            ledger_type=CreditLedgerType.GRANT,
            amount=raw_amount,
            reference=(
                f"Admin analytics allocation for LICENSE subscription {license_sub.id}"
            ),
            metadata={
                "license_subscription_id": str(license_sub.id),
                "allocation_id": str(allocation.id),
                "grant_type": "LICENSE_ADMIN_ANALYTICS",
                "display_amount": LicenseSubscriptionService.ADMIN_ANALYTICS_CREDITS_DISPLAY,
                "raw_amount": raw_amount,
            },
        )

        # Reset overage counter for the new cycle, mirroring teacher
        # enrollment (_enrollment_teacher_internal) for consistency
        wallet.overage_blocks_used = 0
        wallet.save(update_fields=["overage_blocks_used", "updated_at"])

        logger.info(
            "Granted admin analytics allocation (%d display credits) to %s "
            "for license %s",
            LicenseSubscriptionService.ADMIN_ANALYTICS_CREDITS_DISPLAY,
            admin_user.email,
            license_sub.id,
        )

        return allocation

    @staticmethod
    def _invite_and_enroll_one_teacher(
        license_sub: LicenseSubscription,
        school: School,
        admin_user: CustomUser,
        email: str,
    ) -> dict:
        """
        Full enroll for ONE teacher — resolve/create + invite (best-effort,
        deferred email) + SchoolCreditAllocation creation under
        license_sub. NEVER raises; every failure mode is captured and
        returned so every caller (create_license_subscription,
        add_teachers_batch) gets a UNIFORM result shape rather than each
        maintaining its own slightly different try/except (which is how
        this bug family accumulated in the first place — one code path,
        one place to get it right).

        Returns:
            dict: {"email": str, "successful": bool,
                   "teacher_id": str | None, "error": str | None}
        """
        try:
            teacher = LicenseSubscriptionService._get_or_invite_teacher(
                email, school, admin_user, raise_on_conflict=True
            )
            if teacher is None:
                # Only reachable if raise_on_conflict=False, which we
                # never pass here — defensive, not expected in practice.
                return {
                    "email": email,
                    "successful": False,
                    "teacher_id": None,
                    "error": "Teacher could not be resolved or created.",
                }

            LicenseSubscriptionService._enroll_teacher_internal(license_sub, teacher)
            return {
                "email": email,
                "successful": True,
                "teacher_id": str(teacher.id),
                "error": None,
            }
        except (IndividualSubscriptionConflictError, ValueError) as exc:
            logger.warning(
                "Skipped enrolling %s in license %s: %s",
                email,
                license_sub.id,
                exc,
            )
            return {
                "email": email,
                "successful": False,
                "teacher_id": None,
                "error": str(exc),
            }
        except Exception as exc:
            logger.error(
                "Unexpected error enrolling %s in license %s: %s",
                email,
                license_sub.id,
                exc,
                exc_info=True,
            )
            return {
                "email": email,
                "successful": False,
                "teacher_id": None,
                "error": describe_user_error(
                    exc,
                    fallback_message=(
                        "We couldn't enroll this teacher. Please try again."
                    ),
                ),
            }

    @staticmethod
    def _carry_forward_teacher_allocations(
        old_license: LicenseSubscription,
        new_license: LicenseSubscription,
    ) -> list:
        """
        Re-enrolls every currently-active, non-admin teacher from
        old_license into new_license, used when a superadmin replaces a
        school's license — without this, a teacher whose allocation still
        points at the old (about-to-be-deactivated) license would
        silently lose all billing/grading access (see
        resolve_user_billing_context, which filters on
        license_subscription__is_active=True).

        Goes through _enroll_teacher_internal directly (NOT
        _invite_and_enroll_one_teacher) since these are already-onboarded
        CustomUser objects, not raw emails — no invitation email should
        be sent. _enroll_teacher_internal already handles rolling over
        any still-unexpired MONTHLY bucket into a CARRY_OVER bucket, so
        no separate rollover call is needed here.

        NEVER raises; mirrors _invite_and_enroll_one_teacher's per-item
        try/except so one teacher's failure doesn't abort the whole
        batch. Returns the same result shape (plus a "carried_forward"
        marker) so it can be concatenated with invite results into a
        single enrollment_results list.

        Returns:
            list[dict]: [{"email": str, "successful": bool,
                          "teacher_id": str | None, "error": str | None,
                          "carried_forward": True}, ...]
        """
        carried_user_ids = old_license.allocations.filter(
            is_active=True, is_admin_allocation=False
        ).values_list("user_id", flat=True)
        carried_teachers = CustomUser.objects.filter(id__in=list(carried_user_ids))

        results = []
        for teacher in carried_teachers:
            try:
                LicenseSubscriptionService._enroll_teacher_internal(
                    new_license, teacher
                )
                results.append(
                    {
                        "email": teacher.email,
                        "successful": True,
                        "teacher_id": str(teacher.id),
                        "error": None,
                        "carried_forward": True,
                    }
                )
            except (IndividualSubscriptionConflictError, ValueError) as exc:
                logger.warning(
                    "Failed to carry forward teacher %s from license %s " "to %s: %s",
                    teacher.email,
                    old_license.id,
                    new_license.id,
                    exc,
                )
                results.append(
                    {
                        "email": teacher.email,
                        "successful": False,
                        "teacher_id": None,
                        "error": str(exc),
                        "carried_forward": True,
                    }
                )
            except Exception as exc:
                logger.error(
                    "Unexpected error carrying forward teacher %s from "
                    "license %s to %s: %s",
                    teacher.email,
                    old_license.id,
                    new_license.id,
                    exc,
                    exc_info=True,
                )
                results.append(
                    {
                        "email": teacher.email,
                        "successful": False,
                        "teacher_id": None,
                        "error": describe_user_error(
                            exc,
                            fallback_message=(
                                "We couldn't carry this teacher's access "
                                "forward to the new license. They may need "
                                "to be re-added manually."
                            ),
                        ),
                        "carried_forward": True,
                    }
                )
        return results

    @staticmethod
    @transaction.atomic
    def create_license_subscription(
        school: School,
        plan: SubscriptionPlan,
        admin_user: CustomUser,
        teacher_emails: Optional[List[str]] = None,
        contract_months: int = 12,
        max_seats: int = 0,
        custom_price_cents: Optional[int] = None,
        is_active: bool = True,
        auto_renew: bool = True,
        billing_method: str = _DEFAULT_LICENSE_BILLING_METHOD,
        carry_forward_teachers: bool = True,
    ) -> LicenseSubscription:
        """
        Creates a new License subscription for a school.

        This is a comprehensive operation that:
        1. Validates the plan and admin user
        2. Validates contract_months (must be 9, 10, or 12)
        3. Creates the LicenseSubscription with the correct billing period
        4. Creates SchoolCreditAllocations for specified teachers (seat cap enforced)
        5. Initializes CreditWallets for teachers (if they don't exist)
        6. Creates MONTHLY credit buckets for each teacher
        7. Deactivates any conflicting INDIVIDUAL subscriptions
        8. Logs the entire operation

        Args:
            school: School receiving the license
            plan: LICENSE category plan
            admin_user: User managing the license
            teacher_emails: Optional list of teacher emails to enroll immediately
            contract_months: Billing period length (9, 10, or 12). Default 12.
            max_seats: Maximum number of teacher seats (0 = unlimited). Default 0.
            carry_forward_teachers: If this school already has an active
                license, re-enroll its currently-active teachers (and the
                admin's analytics allocation stays untouched — a fresh
                one is always granted below) under the new license with
                no invitation email, instead of silently stranding them
                on the about-to-be-deactivated old license. Default True;
                pass False for an intentional clean-slate replacement.

        Returns:
            LicenseSubscription: The newly created license

        Raises:
            ValueError: If plan or admin validation fails, or contract_months is invalid
            School.DoesNotExist: If school doesn't exist
        """
        # 1. Validate inputs
        LicenseSubscriptionService.validate_license_plan(plan)
        LicenseSubscriptionService.validate_admin_user(admin_user, school)

        # if contract_months not in (1, 9, 10, 12):
        #     raise ValueError(
        #         f"contract_months must be 9, 10, or 12. Got: {contract_months}"
        #     )

        if max_seats <= 0:
            raise ValueError("max_seats must be a positive integer")

        now = timezone.now()
        # Use contract_months to compute the billing window (e.g. 12 months for annual)
        billing_end = now + relativedelta(months=contract_months)

        # 2. Check for existing active license (only one per school) — a
        # plain read, looked up early (before any mutation) so it can
        # inform both the carry-forward set and the combined seat check
        # below.
        existing_license = LicenseSubscription.objects.filter(
            school=school, is_active=True
        ).first()

        # Teachers who will be silently carried forward from the old
        # license, if any — matched by email against teacher_emails so a
        # teacher listed in both isn't double-processed (carried forward
        # once, not ALSO sent through the new-invite path below).
        carry_forward_emails: set = set()
        if existing_license and carry_forward_teachers:
            carried_user_ids = existing_license.allocations.filter(
                is_active=True, is_admin_allocation=False
            ).values_list("user_id", flat=True)
            carry_forward_emails = set(
                CustomUser.objects.filter(id__in=list(carried_user_ids)).values_list(
                    "email", flat=True
                )
            )

        normalized_new_emails = [
            e.strip().lower() for e in (teacher_emails or []) if e and e.strip()
        ]
        genuinely_new_emails = [
            e for e in normalized_new_emails if e not in carry_forward_emails
        ]

        # Validate combined seats (carried-forward + genuinely new)
        # against the cap. Rejects the WHOLE creation rather than
        # truncating — since this method is @transaction.atomic, raising
        # here leaves the old license untouched and active.
        total_requested = len(carry_forward_emails) + len(genuinely_new_emails)
        if max_seats > 0 and total_requested > max_seats:
            if carry_forward_emails:
                raise ValueError(
                    f"Cannot enroll {total_requested} teachers "
                    f"({len(carry_forward_emails)} carried forward from the "
                    f"previous license + {len(genuinely_new_emails)} new): "
                    f"license max_seats is {max_seats}."
                )
            raise ValueError(
                f"Cannot enroll {total_requested} teachers: "
                f"license max_seats is {max_seats}."
            )

        if existing_license:
            logger.warning(
                "School %s already has active license subscription %s. "
                "Deactivating old license before creating new one.",
                school.id,
                existing_license.id,
            )

            # Auto-reject any PENDING offline overage requests against
            # the license about to be deactivated — otherwise they'd sit
            # PENDING forever and only fail confusingly later when a
            # superadmin tries to approve them against an inactive
            # license (see the inactive-license auto-reject branch in
            # approve_overage_offline_request). Uses
            # reject_overage_offline_request (not that inline branch)
            # because it notifies the school admin, which is desired
            # here — they need to know to re-request under the new
            # license.
            pending_overage_requests = list(
                existing_license.overage_offline_requests.filter(
                    status=LicenseOverageOfflineRequestStatus.PENDING
                )
            )
            for pending_request in pending_overage_requests:
                try:
                    LicenseSubscriptionService.reject_overage_offline_request(
                        pending_request,
                        performed_by=admin_user,
                        rejection_reason=(
                            "Automatically rejected: a new license "
                            "subscription was created for this school, "
                            "replacing the license this overage request "
                            "was against. Please submit a new offline "
                            "overage request under the new license if "
                            "still needed."
                        ),
                    )
                except Exception as exc:
                    # Never let a stale/racy overage request block
                    # license creation — log and continue, same
                    # defensive posture as the per-teacher enrollment
                    # loop below.
                    logger.error(
                        "Failed to auto-reject pending overage request %s "
                        "while replacing license %s: %s",
                        pending_request.id,
                        existing_license.id,
                        exc,
                        exc_info=True,
                    )

            existing_license.is_active = False
            existing_license.save(update_fields=["is_active", "updated_at"])

        # 3. Create the LicenseSubscription
        license_sub = LicenseSubscription.objects.create(
            school=school,
            admin_user=admin_user,
            plan=plan,
            contract_months=contract_months,
            max_seats=max_seats,
            billing_cycle_start=now,
            billing_cycle_end=billing_end,
            is_active=True,
            auto_renew=auto_renew,
            custom_price_cents=custom_price_cents,
            total_credits_consumed=0,
            billing_method=billing_method,
        )

        logger.info(
            "Created LicenseSubscription %s for school %s with plan %s "
            "(contract: %d months, max_seats: %s, billing cycle: %s to %s)",
            license_sub.id,
            school.name,
            plan.name,
            contract_months,
            max_seats if max_seats > 0 else "unlimited",
            now,
            billing_end,
        )

        # 4. Grant the admin their own fixed analytics-credit allocation.
        # Deliberately NOT wrapped in a try/except (unlike the per-teacher
        # loop below) — a failure here should fail the whole license
        # creation rather than silently leave the admin without any
        # credits for their dashboard's AI-generated analytics.
        LicenseSubscriptionService._grant_admin_allocation(license_sub)

        enrollment_results = []

        # Carry forward active teachers from the old license (if any,
        # and not opted out) BEFORE the new-invite loop, so that loop
        # only ever processes genuinely_new_emails — teachers already
        # carried forward never receive a duplicate enrollment or an
        # invitation email.
        if existing_license and carry_forward_teachers and carry_forward_emails:
            enrollment_results.extend(
                LicenseSubscriptionService._carry_forward_teacher_allocations(
                    existing_license, license_sub
                )
            )

        for email in genuinely_new_emails:
            enrollment_results.append(
                LicenseSubscriptionService._invite_and_enroll_one_teacher(
                    license_sub, school, admin_user, email
                )
            )

        successful_count = sum(1 for r in enrollment_results if r["successful"])
        failed_results = [r for r in enrollment_results if not r["successful"]]

        if failed_results:
            logger.error(
                "License %s creation: %d/%d teacher invitations FAILED: %s",
                license_sub.id,
                len(failed_results),
                len(enrollment_results),
                failed_results,
            )

        # Transient, non-persisted summary attached to this in-memory
        # instance only — lets BOTH callers (the OFFLINE view branch,
        # and the STRIPE webhook handler) report or log exactly what
        # happened per teacher, without changing this method's return
        # type (which must stay a bare model instance — the
        # DRF-serializer call site depends on that contract).
        license_sub._teacher_enrollment_results = {
            "successful": successful_count,
            "failed": len(failed_results),
            "errors": [
                {"email": r["email"], "error": r["error"]} for r in failed_results
            ],
        }

        logger.info(
            "LicenseSubscription %s creation complete. Enrolled %d/%d "
            "requested teachers.",
            license_sub.id,
            successful_count,
            len(enrollment_results),
        )

        return license_sub

    @staticmethod
    @transaction.atomic
    def _get_or_invite_teacher(
        email: str,
        school: School,
        admin_user: CustomUser,
        raise_on_conflict: bool = False,
    ) -> CustomUser | None:
        """
        Find an existing teacher by email, or create an inactive teacher account
        and send an activation email

        Raises ValueError if email is invalid, teacher belongs to a different school,
        or the email is already used by a non-teacher account
        """

        email = email.strip().lower()
        if not email:
            raise ValueError("Teacher email is required")

        # 1. Business email validation
        if not is_exempt_email_domain(email) and not is_business_email(email):
            error_msg = f"Email {email} is not a business email. Only business emails are allowed."

            if raise_on_conflict:
                raise ValueError(error_msg)
            logger.warning(error_msg)

            return None

        # Check if user with this email already exists
        user = CustomUser.objects.filter(email=email).first()

        if user:
            # 2. Validate user type
            if user.user_type != UserTypes.TEACHER:
                error_msg = f"Email {email} already belongs to a {user.user_type} account, not a teacher."

                if raise_on_conflict:
                    raise ValueError(error_msg)
                logger.warning(error_msg)
                return None

            # 3. Check for active individual subscription
            has_individual_sub = user.subscriptions.filter(is_active=True).exists()

            if has_individual_sub:
                error_msg = (
                    f"Teacher {email} has an active individual subscription. "
                    "Individual subscriptions cannot be converted to a license. "
                    "Please cancel the individual subscription first."
                )

                if raise_on_conflict:
                    raise IndividualSubscriptionConflictError(error_msg)
                logger.warning(error_msg)
                return None

            # 4. School validation
            if user.school and user.school != school:
                error_msg = (
                    f"Teacher {email!r} already belongs to school {user.school.name!r}. "
                    f"Cannot enroll under {school.name!r}."
                )

                if raise_on_conflict:
                    raise ValueError(error_msg)
                logger.warning(error_msg)
                return None

            # Associate the teacher with the school if they don't have one
            if not user.school:
                user.school = school
                user.save(update_fields=["school"])

            # 5. If user exists but inactive, ALWAYS (re-)send the
            # invitation email, regardless of whether the previous
            # activation token has already expired.
            #
            if not user.is_active:
                if (
                    not user.activation_token
                    or user.activation_expires < timezone.now()
                ):
                    user.activation_token = otp_manager.generate_otp()
                    user.activation_expires = timezone.now() + timedelta(days=7)
                    user.save(update_fields=["activation_token", "activation_expires"])

                LicenseSubscriptionService._send_teacher_invitation(
                    user, school, admin_user
                )

            return user

        activation_token = otp_manager.generate_otp()
        try:
            # Create new teacher account (inactive)
            set_license_invitation_context(True)
            user = CustomUser.objects.create(
                email=email,
                user_type=UserTypes.TEACHER,
                school=school,
                is_active=False,
                registration_method=RegistrationMethod.EMAIL,
                activation_token=activation_token,
                activation_expires=timezone.now() + timedelta(days=7),
            )

            # Set a dummy unusable password (they will set it via activation)
            user.set_unusable_password()
            user.save()
        finally:
            clear_license_invitation_context()

        # Send invitation email
        LicenseSubscriptionService._send_teacher_invitation(user, school, admin_user)
        return user

    @staticmethod
    def _send_teacher_invitation(
        teacher: CustomUser, school: School, admin_user: CustomUser
    ) -> None:

        frontend_domain = settings.FRONTEND_DOMAIN
        activation_link = (
            f"https://{frontend_domain}/register/teacher?"
            f"token={teacher.activation_token}&email={teacher.email}"
        )

        merge_data = {
            "title": f"You have been added to {school.name} as a teacher",
            "name": teacher.get_full_name() or "Teacher",
            "top_content": (
                f"{admin_user.get_full_name()} has invited you to teach at {school.name}.\n\n"
                "Complete your registration to set up your password and start using Grade A+."
            ),
            "bottom_content": "This invitation link expires in 7 days.",
            "activation_url": activation_link,
            "current_year": timezone.now().year,
            "support_email": settings.SUPPORT_EMAIL,
        }

        teacher_email = teacher.email
        school_name = school.name

        def _dispatch():
            try:
                send_email_task.delay(
                    subject=f"Invitation to teach at {school_name}",
                    message="",
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[teacher_email],
                    html_message=None,
                    template_id="ynrw7gy0ye2l2k8e",
                    merge_data=merge_data,
                )
                logger.info(
                    "Queued teacher invitation email to %s for school %s.",
                    teacher_email,
                    school_name,
                )
            except Exception:
                logger.exception(
                    "Failed to queue teacher invitation email to %s for "
                    "school %s. The teacher account/allocation were still "
                    "created successfully — this can be resolved by "
                    "re-inviting the same email via add-teachers.",
                    teacher_email,
                    school_name,
                )

        transaction.on_commit(_dispatch)

    @staticmethod
    def _enroll_teacher_internal(
        license_sub: LicenseSubscription, teacher: CustomUser
    ) -> SchoolCreditAllocation:
        """
        Internal method to enroll a single teacher in a license.

        Handles:
        1. Creating SchoolCreditAllocation
        2. Ensuring CreditWallet exists
        3. Computing capped grant based on global consumption cap
        4. Creating MONTHLY bucket (capped amount)
        5. Deactivating conflicting INDIVIDUAL subscriptions
        6. Rollover from previous MONTHLY bucket if transitioning
        7. Audit logging

        Args:
            license_sub: License to enroll teacher into
            teacher: Teacher to enroll

        Returns:
            SchoolCreditAllocation: The created allocation

        Raises:
            ValueError: If allocation already exists or teacher validation fails
        """

        license_sub = LicenseSubscription.objects.select_for_update().get(
            pk=license_sub.pk
        )

        if teacher.school != license_sub.school:
            raise ValueError(
                f"Teacher {teacher.email} does not belong to school "
                f"{license_sub.school.name}. Cannot enroll."
            )

        # 4. Check for and handle existing INDIVIDUAL subscriptions
        active_individual_sub = teacher.subscriptions.filter(is_active=True).exists()
        if active_individual_sub:
            error_msg = (
                f"Teacher {teacher.email} has an active individual subscription. "
                "Individual subscriptions cannot be converted to a license. "
                "Please cancel the individual subscription first."
            )
            logger.warning(error_msg)
            raise IndividualSubscriptionConflictError(error_msg)

        now = timezone.now()

        # 1. Check if teacher is already enrolled
        existing = SchoolCreditAllocation.objects.filter(
            license_subscription=license_sub, user=teacher
        ).first()

        if existing and existing.is_active:
            logger.warning(
                "Teacher %s is already actively enrolled in license %s. Skipping.",
                teacher.email,
                license_sub.id,
            )
            return existing

        # 1b. Enforce max_seats cap (skip cap check if this is a reactivation)
        if not (existing and not existing.is_active):
            seats_remaining = license_sub.seats_remaining
            if seats_remaining is not None and seats_remaining <= 0:
                raise ValueError(
                    f"License {license_sub.id!r} for school {license_sub.school.name!r} "
                    f"has reached its seat limit of {license_sub.max_seats!r}. "
                    f"Upgrade the license or remove an existing teacher to enroll a new one."
                )

        # 2. Create or reactivate allocation
        allocation, created = SchoolCreditAllocation.objects.get_or_create(
            license_subscription=license_sub,
            user=teacher,
            defaults={
                "monthly_allocation": license_sub.plan.monthly_credits,
                "is_active": True,
            },
        )

        if not created and not allocation.is_active:
            # Reactivating a previously removed teacher
            allocation.is_active = True
            allocation.monthly_allocation = license_sub.plan.monthly_credits
            allocation.save(
                update_fields=["is_active", "monthly_allocation", "updated_at"]
            )
            logger.info(
                "Reactivated teacher %s in license %s",
                teacher.email,
                license_sub.id,
            )
        elif created:
            logger.info(
                "Enrolled teacher %s in license %s with allocation %d credits",
                teacher.email,
                license_sub.id,
                allocation.monthly_allocation,
            )

        queue_sync(teacher)

        # 3. Ensure teacher's CreditWallet exists
        wallet, wallet_created = CreditWallet.objects.get_or_create(user=teacher)
        if wallet_created:
            logger.info("Created CreditWallet for teacher %s", teacher.email)

        max_seats = license_sub.max_seats

        if max_seats == 0:
            # Unlimited seats - no cap, grant full allocation
            grant_amount = allocation.monthly_allocation
            total_budget = None
            consumed = None
        else:
            total_budget = max_seats * license_sub.plan.monthly_credits
            consumed = license_sub.total_credits_consumed
            remaining_budget = total_budget - consumed

            if remaining_budget <= 0:
                # No budget left - no credits to grant
                grant_amount = 0
            else:
                grant_amount = min(allocation.monthly_allocation, remaining_budget)

        # 5. Handle existing MONTHLY bucket (from previous subscription or license)
        existing_monthly = wallet.buckets.filter(
            bucket_type=CreditBucketType.MONTHLY,
            expires_at__gt=now,
        ).first()

        if existing_monthly and created:
            # New allocation but teacher had credits from previous subscription
            # Expire the old bucket and create rollover
            unused = existing_monthly.remaining_credits
            if unused > 0:
                rollover_amount, cap_meta = wallet.compute_capped_rollover(
                    license_sub.plan, unused, monthly_amount=grant_amount, now=now
                )

                if rollover_amount > 0:
                    expiry = now + relativedelta(
                        months=license_sub.plan.carry_over_expiry_months
                    )
                    carry_bucket = CreditBucket.objects.create(
                        wallet=wallet,
                        bucket_type=CreditBucketType.CARRY_OVER,
                        total_credits=rollover_amount,
                        used_credits=0,
                        expires_at=expiry,
                    )

                    CreditLedger.objects.create(
                        user=teacher,
                        bucket=carry_bucket,
                        ledger_type=CreditLedgerType.GRANT,
                        amount=rollover_amount,
                        reference=(
                            f"Rollover from previous subscription "
                            f"when transitioning to LICENSE {license_sub.id}"
                        ),
                        metadata={
                            "previous_unused": unused,
                            "rollover_percent": str(
                                license_sub.plan.carry_over_percent
                            ),
                            "license_id": str(license_sub.id),
                            **cap_meta,
                        },
                    )
                    logger.info(
                        "Carried over %d credits for teacher %s when "
                        "transitioning to license",
                        rollover_amount,
                        teacher.email,
                    )
                elif cap_meta["requested_rollover"] > 0:
                    logger.info(
                        "Rollover fully suppressed by max_bank for teacher "
                        "%s transitioning to license %s: requested %d (%s).",
                        teacher.email,
                        license_sub.id,
                        cap_meta["requested_rollover"],
                        cap_meta,
                    )

            # Expire the old bucket
            existing_monthly.expires_at = now
            existing_monthly.save(update_fields=["expires_at", "updated_at"])
            logger.info(
                "Expired old MONTHLY bucket for teacher %s",
                teacher.email,
            )

        now = timezone.now()

        # Set the first refresh date to exactly one month from now
        next_refresh = now + relativedelta(months=1)

        allocation.next_credit_grant_at = next_refresh
        allocation.save(update_fields=["next_credit_grant_at", "updated_at"])

        # 6. Create new MONTHLY bucket for the license allocation
        monthly_bucket = CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=grant_amount,
            used_credits=0,
            expires_at=next_refresh,
        )

        # 7. Create audit ledger entry with the actual grant amount
        is_capped = grant_amount < allocation.monthly_allocation
        CreditLedger.objects.create(
            user=teacher,
            bucket=monthly_bucket,
            ledger_type=CreditLedgerType.GRANT,
            amount=grant_amount,
            reference=(
                f"Allocation for LICENSE subscription {license_sub.id} "
                f"({license_sub.plan.display_name or license_sub.plan.name})"
                f"{' (capped)' if is_capped else ''}"
            ),
            metadata={
                "license_subscription_id": str(license_sub.id),
                "school_id": str(license_sub.school.id),
                "allocation_id": str(allocation.id),
                "teacher_email": teacher.email,
                "global_budget": total_budget if max_seats != 0 else None,
                "consumed_before": consumed if max_seats != 0 else None,
                "grant_amount": grant_amount,
                "is_capped": is_capped,
            },
        )

        logger.info(
            "Created MONTHLY credit bucket with %d credits for teacher %s "
            "under license %s (capped=%s)",
            grant_amount,
            teacher.email,
            license_sub.id,
            is_capped,
        )

        # 8. Reset overage blocks (if transitioning from individual)
        wallet.overage_blocks_used = 0
        wallet.save(update_fields=["overage_blocks_used"])

        return allocation

    @staticmethod
    @transaction.atomic
    def add_teacher_to_license(
        license_sub: LicenseSubscription,
        teacher_email: str,
    ) -> SchoolCreditAllocation:
        """
        Adds a teacher to an existing License subscription.

        Args:
            license_sub: License subscription to add teacher to
            teacher: Teacher to add

        Returns:
            SchoolCreditAllocation: The allocation

        Raises:
            ValueError: If license is not active
        """
        if not license_sub.is_active:
            raise ValueError(
                f"Cannot add teachers to inactive license subscription {license_sub.id}"
            )

        # Lock license row
        license_sub = LicenseSubscription.objects.select_for_update().get(
            pk=license_sub.pk
        )

        # Check if already active
        user = CustomUser.objects.filter(email=teacher_email).first()
        if user and license_sub.allocations.filter(user=user, is_active=True).exists():
            raise ValueError(
                f"Teacher {teacher_email} is already active under this license."
            )

        # Check seats
        seats_remaining = license_sub.seats_remaining
        if seats_remaining is not None and seats_remaining <= 0:
            raise ValueError("No seats remaining to add a new teacher.")

        teacher = LicenseSubscriptionService._get_or_invite_teacher(
            teacher_email,
            license_sub.school,
            license_sub.admin_user,
            raise_on_conflict=True,
        )

        return LicenseSubscriptionService._enroll_teacher_internal(license_sub, teacher)

    @staticmethod
    @transaction.atomic
    def add_teachers_batch(
        license_sub: LicenseSubscription,
        teacher_emails: List[str],
    ) -> dict:
        """
        Adds multiple teachers to a License subscription in a single transaction.

        Args:
            license_sub: License subscription
            teacher_emails: List of teacher emails

        Returns:
            dict: {
                'successful': int,
                'failed': int,
                'errors': [{'teacher_id': str, 'error': str}]
            }
        """
        if not license_sub.is_active:
            raise ValueError(
                f"Cannot add teachers to inactive license subscription {license_sub.id}"
            )

        # Lock License row to prevent concurrent modification
        license_sub = LicenseSubscription.objects.select_for_update().get(
            pk=license_sub.pk
        )

        # Get active teacher IDs under this license
        active_teacher_ids = set(
            license_sub.allocations.filter(is_active=True).values_list(
                "user_id", flat=True
            )
        )

        # Determine which emails are NOT already active
        new_teacher_emails = []

        for email in teacher_emails:
            user = CustomUser.objects.filter(email=email).first()
            if user and user.id in active_teacher_ids:
                # Already active - skip (they won't consume a seat)
                continue
            new_teacher_emails.append(email)

        # Check seats
        seats_remaining = license_sub.seats_remaining
        if seats_remaining is not None and len(new_teacher_emails) > seats_remaining:
            raise ValueError(
                f"Not enough seats available. Need {len(new_teacher_emails)}, only {seats_remaining} remaining."
            )

        results: Dict[str, Any] = {"successful": 0, "failed": 0, "errors": []}

        for email in new_teacher_emails:
            result = LicenseSubscriptionService._invite_and_enroll_one_teacher(
                license_sub, license_sub.school, license_sub.admin_user, email
            )
            if result["successful"]:
                results["successful"] += 1
            else:
                results["failed"] += 1
                results["errors"].append(
                    {"teacher_email": email, "error": result["error"]}
                )

        logger.info(
            "Batch added teachers to license %s: %d successful, %d failed",
            license_sub.id,
            results["successful"],
            results["failed"],
        )

        return results

    @staticmethod
    @transaction.atomic
    def remove_teacher_from_license(
        license_sub: LicenseSubscription,
        teacher: CustomUser,
    ) -> None:
        """
        Removes a teacher from a License subscription.

        1. Marks the SchoolCreditAllocation as inactive.
        2. Expires all active credit buckets (MONTHLY, CARRY_OVER, OVERAGE, etc.)
           so the teacher cannot use them.
        3. Logs the operation.

        The teacher's wallet and historical buckets remain for audit purposes.
        If the teacher later re‑enrolls (via license or individual subscription),
        new buckets will be created.
        """

        # Lock the allocation row to prevent race conditions
        allocation = (
            SchoolCreditAllocation.objects.filter(
                license_subscription=license_sub,
                user=teacher,
                is_active=True,
            )
            .select_for_update()
            .first()
        )

        if not allocation:
            raise ValueError(
                f"Teacher {teacher.email} is not actively enrolled in "
                f"license {license_sub.id}"
            )

        # 1. Deactivate allocation
        allocation.is_active = False
        allocation.save(update_fields=["is_active", "updated_at"])

        from users.tasks import sync_user_to_mailerlite

        sync_user_to_mailerlite.delay(str(teacher.id))

        # 2. Expire all active credit buckets for this teacher
        wallet = teacher.credit_wallet
        now = timezone.now()

        expired_count = wallet.buckets.filter(
            models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=now)
        ).update(expires_at=now)

        logger.info(
            "Removed teacher %s from license %s" "Expired %d credit buckets.",
            teacher.email,
            license_sub.id,
            expired_count,
        )

    @staticmethod
    @transaction.atomic
    def process_license_renewal(license_sub: LicenseSubscription) -> None:
        """
        Processes renewal for a License subscription at the end of billing cycle.

        For each enrolled teacher, this operation:
        1. Applies rollover % to their unused credits
        2. Expires old MONTHLY bucket
        3. Creates new MONTHLY bucket with fresh allocation
        4. Logs all transitions

        This is called by Celery tasks and handles renewal atomically
        across all enrolled teachers, with per‑teacher savepoints to prevent
        total failure if one teacher encounters an error.
        """
        now = timezone.now()

        # Lock the license row to prevent concurrent renewals
        license_sub = LicenseSubscription.objects.select_for_update().get(
            pk=license_sub.pk
        )

        # Idempotency check: if already renewed (billing_cycle_end in the future), skip
        if license_sub.billing_cycle_end > now:
            logger.info(
                "License %s already renewed (billing_cycle_end=%s > now). Skipping.",
                license_sub.id,
                license_sub.billing_cycle_end,
            )
            return

        if not license_sub.is_active:
            logger.warning(
                "Renewal requested for inactive license %s. Skipping.",
                license_sub.id,
            )
            return

        if not license_sub.auto_renew:
            logger.info(
                "License %s has auto_renew=False. Deactivating.",
                license_sub.id,
            )
            license_sub.is_active = False
            license_sub.save(update_fields=["is_active", "updated_at"])
            sync_teachers_under_license_to_mailerlite(license_sub)
            return

        # Get all active allocations
        active_allocations = list(
            license_sub.allocations.filter(is_active=True).select_related(
                "user__credit_wallet"
            )
        )

        renewal_start = now
        renewal_end = now + relativedelta(months=license_sub.contract_months)

        renewal_count = 0
        failed_teachers = []

        for allocation in active_allocations:
            # Use a nested savepoint so failure of one teacher doesn't rollback the whole transaction
            with transaction.atomic():
                try:
                    teacher = allocation.user
                    wallet = teacher.credit_wallet

                    LicenseSubscriptionService._rollover_and_grant_monthly_bucket(
                        teacher=teacher,
                        wallet=wallet,
                        plan=license_sub.plan,
                        grant_amount=allocation.monthly_allocation,
                        new_expiry=now + relativedelta(months=1),
                        now=now,
                        reference=(
                            f"Renewal allocation for LICENSE subscription {license_sub.id} "
                            f"(cycle {renewal_start} to {renewal_end})"
                        ),
                        metadata={
                            "license_subscription_id": str(license_sub.id),
                            "allocation_id": str(allocation.id),
                            "cycle_start": renewal_start.isoformat(),
                            "cycle_end": renewal_end.isoformat(),
                        },
                    )

                    allocation.next_credit_grant_at = now + relativedelta(months=1)
                    allocation.save(
                        update_fields=["next_credit_grant_at", "updated_at"]
                    )

                    wallet.overage_blocks_used = 0
                    wallet.save(update_fields=["overage_blocks_used", "updated_at"])

                    renewal_count += 1

                except Exception as e:
                    logger.error(
                        "Failed to renew credits for teacher %s under license %s: %s",
                        allocation.user.email,
                        license_sub.id,
                        str(e),
                    )
                    failed_teachers.append(allocation.user.email)

        # 4. Update license cycle dates only if at least one teacher renewed successfully
        # (or you may choose to update even if all failed, but that would be odd)
        if renewal_count > 0 or not active_allocations:
            license_sub.billing_cycle_start = renewal_start
            license_sub.billing_cycle_end = renewal_end
            license_sub.save(
                update_fields=["billing_cycle_start", "billing_cycle_end", "updated_at"]
            )

            license_sub.total_credits_consumed = 0
            license_sub.save(update_fields=["total_credits_consumed", "updated_at"])
        else:
            # No teacher could be renewed – deactivate the license to avoid endless retries
            logger.error(
                "License %s renewal failed for all %d teachers. Deactivating license.",
                license_sub.id,
                len(active_allocations),
            )
            license_sub.is_active = False
            license_sub.save(update_fields=["is_active", "updated_at"])
            sync_teachers_under_license_to_mailerlite(license_sub)
            raise RuntimeError(
                f"License {license_sub.id} renewal failed for all teachers."
            )

        logger.info(
            "Completed renewal for license %s: renewed credits for %d teachers. Failed: %s",
            license_sub.id,
            renewal_count,
            failed_teachers,
        )

    @staticmethod
    @transaction.atomic
    def update_license_plan(
        license_sub: LicenseSubscription,
        new_plan: SubscriptionPlan,
    ) -> None:
        """
        Updates a License subscription to a new plan.

        This changes the plan for the license. New teachers added after this
        will receive allocations from the new plan.

        Existing teachers keep their current allocation in the billing cycle.
        Allocations will be updated on the next renewal.

        Args:
            license_sub: License to update
            new_plan: New plan (must be LICENSE category)

        Raises:
            ValueError: If new_plan is not LICENSE category
        """
        LicenseSubscriptionService.validate_license_plan(new_plan)

        old_plan = license_sub.plan

        # Update the license's plan
        license_sub.plan = new_plan
        license_sub.save(update_fields=["plan", "updated_at"])

        # Update monthly_allocation for all active teachers under this license
        # This ensures that at the next renewal they receive the correct amount
        active_allocations = license_sub.allocations.filter(
            is_active=True, is_admin_allocation=False
        )

        for allocation in active_allocations:
            allocation.monthly_allocation = new_plan.monthly_credits
            allocation.save(update_fields=["monthly_allocation", "updated_at"])

            # Add a ledger entry to audit the change
            CreditLedger.objects.create(
                user=allocation.user,
                bucket=None,  # Not lonked to a specific bucket, just Metadata
                ledger_type=CreditLedgerType.PLAN_CHANGE,
                amount=0,
                reference=f"Plan changed from {old_plan.name} to {new_plan.name} -",
                metadata={
                    "license_subscription_id": str(license_sub.id),
                    "old_monthly_allocation": old_plan.monthly_credits,
                    "new_monthly_allocation": new_plan.monthly_credits,
                },
            )

        logger.info(
            "Updated license %s plan from %s to %s. "
            "Existing teachers keep current allocation until next renewal.",
            license_sub.id,
            old_plan.name,
            new_plan.name,
            active_allocations.count(),
        )

        sync_teachers_under_license_to_mailerlite(license_sub)

    @staticmethod
    def cancel_license_subscription(license_sub: LicenseSubscription) -> None:
        """
        Cancels a License subscription.

        Sets is_active=False. Teachers keep their current credits until
        billing cycle end, but the license won't auto-renew.

        Args:
            license_sub: License to cancel
        """
        license_sub.is_active = False
        license_sub.auto_renew = False
        license_sub.save(update_fields=["is_active", "auto_renew", "updated_at"])

        sync_teachers_under_license_to_mailerlite(license_sub)

        logger.info(
            "Cancelled license subscription %s for school %s. "
            "Teachers will lose access when billing cycle ends.",
            license_sub.id,
            license_sub.school.name,
        )

    @staticmethod
    def get_teacher_allocation_info(teacher: CustomUser) -> Optional[dict]:
        """
        Gets allocation information if teacher is under a License.

        Returns:
            dict with keys:
                - license_id (UUID)
                - school_name (str)
                - plan_name (str)
                - monthly_allocation (int, raw)
                - current_balance (int, raw)
                - admin_email (str)
                - billing_cycle_end (datetime)
            or None if teacher is not under a license
        """
        allocation = (
            teacher.school_credit_allocations.filter(
                is_active=True,
                license_subscription__is_active=True,
            )
            .select_related(
                "license_subscription__school",
                "license_subscription__plan",
                "license_subscription__admin_user",
                "user__credit_wallet",
            )
            .first()
        )

        if not allocation:
            return None

        license_sub = allocation.license_subscription
        wallet = teacher.credit_wallet

        return {
            "license_id": str(license_sub.id),
            "school_name": license_sub.school.name,
            "plan_name": license_sub.plan.display_name or license_sub.plan.name,
            "monthly_allocation": allocation.monthly_allocation,
            "monthly_allocation_display": allocation.display_monthly_allocation,
            "current_balance": wallet.total_remaining_credits(),
            "current_balance_display": wallet.display_balance,
            "admin_email": license_sub.admin_user.email,
            "admin_name": license_sub.admin_user.get_full_name(),
            "billing_cycle_end": license_sub.billing_cycle_end,
            "is_auto_renew": license_sub.auto_renew,
        }

    @staticmethod
    @transaction.atomic
    def change_license_plan(
        license_sub: LicenseSubscription,
        new_plan: SubscriptionPlan,
        custom_price_cents: Optional[int] = None,
        remove_custom_price: bool = False,
        performed_by: Optional[CustomUser] = None,
    ) -> LicenseSubscription:
        # Lock license
        license_sub = LicenseSubscription.objects.select_for_update().get(
            pk=license_sub.pk
        )

        old_plan = license_sub.plan
        old_effective_price, new_effective_price, new_custom_price_cents = (
            LicenseSubscriptionService._resolve_effective_price(
                license_sub, new_plan, custom_price_cents, remove_custom_price
            )
        )

        # If the effective price is unchanged and plan is same, maybe skip? But we still need to update plan if changed.
        if old_plan.id == new_plan.id and old_effective_price == new_effective_price:
            raise ValueError("License is already on this plan with the same price.")

        # Update local license plan and custom price
        license_sub.plan = new_plan
        license_sub.custom_price_cents = new_custom_price_cents
        license_sub.save(update_fields=["plan", "custom_price_cents", "updated_at"])

        # Update allocations
        active_allocations = license_sub.allocations.filter(
            is_active=True, is_admin_allocation=False
        )

        for allocation in active_allocations:
            allocation.monthly_allocation = new_plan.monthly_credits
            allocation.save(update_fields=["monthly_allocation", "updated_at"])

            # Log plan change in ledger
            CreditLedger.objects.create(
                user=allocation.user,
                bucket=None,
                ledger_type=CreditLedgerType.PLAN_CHANGE,
                amount=0,
                reference=f"License plan changed from {old_plan.name} to {new_plan.name}",
                metadata={
                    "license_subscription_id": str(license_sub.id),
                    "old_plan": old_plan.name,
                    "new_plan": new_plan.name,
                    "old_monthly_allocation": old_plan.monthly_credits,
                    "new_monthly_allocation": new_plan.monthly_credits,
                    "old_custom_price_cents": license_sub.custom_price_cents,  # after update? careful
                    "new_custom_price_cents": new_custom_price_cents,
                    "old_effective_price": int(old_effective_price),
                    "new_effective_price": int(new_effective_price),
                },
            )

        # Sync to Stripe ONLY if this license is actually Stripe-billed.
        # Offline licenses record the change for accounting instead

        if license_sub.billing_method == LicenseBillingMethod.STRIPE:
            # Call Stripe to change price
            try:
                from .stripe_service import StripeSubscriptionMutationService

                StripeSubscriptionMutationService.change_license_price(
                    license_sub,
                    new_plan,
                    new_custom_price_cents,
                    performed_by=performed_by,
                )
            except ValueError as e:
                raise ValueError(f"Stripe price change failed: {e}") from e
        else:
            billing_record = LicenseBillingRecord.objects.create(
                license_subscription=license_sub,
                record_type=LicenseBillingRecordType.PLAN_CHANGE_OFFLINE,
                amount_paid_cents=new_custom_price_cents,
                notes=(
                    f"Plan changed from {old_plan.name} to {new_plan.name} "
                    "(offline license — adjust the school's invoice accordingly)."
                ),
                performed_by=performed_by,
            )

            BillingTransactionService.record(
                source=BillingTransactionSource.LICENSE,
                transaction_type=BillingTransactionType.LICENSE_OFFLINE_PLAN_CHANGE,
                status=BillingTransactionStatus.MANUAL,
                billing_method=BillingTransactionMethod.OFFLINE,
                amount_cents=new_custom_price_cents or 0,
                license_subscription=license_sub,
                license_billing_record=billing_record,
                performed_by=performed_by,
                description=f"Offline plan change {old_plan.name} -> {new_plan.name}",
                occurred_at=timezone.now(),
            )

        logger.info(
            "License %s plan changed from %s to %s. Custom price: %s. Allocations updated: %d.",
            license_sub.id,
            old_plan.name,
            new_plan.name,
            new_custom_price_cents,
            active_allocations.count(),
        )

        sync_teachers_under_license_to_mailerlite(license_sub)

        return license_sub

    @staticmethod
    @transaction.atomic
    def update_seats(
        license_sub: LicenseSubscription,
        new_max_seats: int,
        performed_by: Optional[CustomUser] = None,
    ) -> LicenseSubscription:
        """
        Update the maximum number of seats for a license.
        Validates that new_max_seats >= current active teacher count.
        Updates Stripe subscription quantity with appropriate proration.
        """
        if new_max_seats <= 0:
            raise ValueError("max_seats must be a positive integer.")

        # Lock license row
        license_sub = LicenseSubscription.objects.select_for_update().get(
            pk=license_sub.pk
        )

        # Get active teacher count
        active_teacher_count = license_sub.allocations.filter(
            is_active=True, is_admin_allocation=False
        ).count()

        if new_max_seats < active_teacher_count:
            raise ValueError(
                f"Cannot reduce max_seats to {new_max_seats} because there are {active_teacher_count} active teachers. "
                "Remove some teachers first."
            )

        if new_max_seats == license_sub.max_seats:
            raise ValueError("License already has this many seats.")

        old_seats = license_sub.max_seats
        is_increase = new_max_seats > old_seats
        proration_behavior = "always_invoice" if is_increase else "none"

        # Update Stripe subscription quantity
        if license_sub.stripe_subscription_id:
            try:
                # Retrieve subscription item ID
                stripe_sub = stripe.Subscription.retrieve(
                    license_sub.stripe_subscription_id
                )
                items = stripe_sub.get("items", {}).get("data", [])
                if not items:
                    raise ValueError("Stripe subscription has no items.")
                item_id = items[0]["id"]

                # Update quantity
                stripe.Subscription.modify(
                    license_sub.stripe_subscription_id,
                    items=[{"id": item_id, "quantity": new_max_seats}],
                    proration_behavior=proration_behavior,
                )

                # For always_invoice, verify invoice paid
                if proration_behavior == "always_invoice":
                    stripe_sub_refreshed = stripe.Subscription.retrieve(
                        license_sub.stripe_subscription_id
                    )
                    latest_invoice_id = stripe_sub_refreshed.get("latest_invoice")
                    if latest_invoice_id:
                        invoice = stripe.Invoice.retrieve(
                            latest_invoice_id, expand=["payment_intent"]
                        )
                        if invoice.get("status") != "paid":
                            # Revert quantity
                            stripe.Subscription.modify(
                                license_sub.stripe_subscription_id,
                                items=[{"id": item_id, "quantity": old_seats}],
                                proration_behavior="none",
                            )
                            raise ValueError(
                                f"Seat increase payment failed (invoice status: {invoice['status']}). "
                                "Seats have not been increased."
                            )

                        BillingTransactionService.record(
                            source=BillingTransactionSource.LICENSE,
                            transaction_type=BillingTransactionType.LICENSE_SEAT_CHANGE_CHARGE,
                            status=BillingTransactionStatus.PAID,
                            billing_method=BillingTransactionMethod.STRIPE,
                            amount_cents=invoice.get("amount_paid") or 0,
                            currency=invoice.get("currency", "usd"),
                            license_subscription=license_sub,
                            stripe_invoice_id=latest_invoice_id,
                            stripe_subscription_id=license_sub.stripe_subscription_id,
                            receipt_url=invoice.get("hosted_invoice_url"),
                            performed_by=performed_by,
                            description=f"Seats increased {old_seats} -> {new_max_seats}",
                        )
            except stripe.error.StripeError as exc:
                raise ValueError(f"Stripe error while updating seats: {exc}") from exc

        # Update local max_seats
        license_sub.max_seats = new_max_seats
        license_sub.save(update_fields=["max_seats", "updated_at"])

        if license_sub.billing_method == LicenseBillingMethod.OFFLINE:
            LicenseBillingRecord.objects.create(
                license_subscription=license_sub,
                record_type=LicenseBillingRecordType.SEATS_CHANGE_OFFLINE,
                notes=(
                    f"Seats changed {old_seats} -> {new_max_seats} "
                    "(offline license — adjust invoicing accordingly)."
                ),
                performed_by=performed_by,
            )

        # Log the change
        logger.info(
            "License %s seats updated: %d -> %d (proration: %s)",
            license_sub.id,
            old_seats,
            new_max_seats,
            proration_behavior,
        )

        return license_sub

    @staticmethod
    def initiate_overage_purchase(
        license_sub: LicenseSubscription,
        requesting_user,
        total_blocks: int,
        allocations: dict,
        success_url: Optional[str] = None,
        cancel_url: Optional[str] = None,
        payment_method: str = "stripe",
    ) -> dict:
        """
        SINGLE entry point for purchasing/granting teacher overage blocks
        under a license, for BOTH actor types:

          - Super admin -> immediate, atomic OFFLINE grant. No Stripe
            charge. Returns {"action": "granted", ...}.
          - The license's own admin_user (school admin), payment_method=
            "stripe" (default) -> creates a Stripe Checkout Session and
            returns immediately with NOTHING granted yet. Returns
            {"action": "checkout", ...}. Credits are granted only once
            checkout.session.completed confirms payment (see
            StripeWebhookHandler._handle_license_overage_checkout_
            completed).
          - The license's own admin_user, payment_method="offline_request"
            -> records a LicenseOverageOfflineRequest for superadmin
            review (paying outside Stripe — bank transfer, invoice,
            cash). Returns {"action": "offline_request_pending", ...}.
            NOTHING is granted here either — see
            approve_overage_offline_request.

        This works identically regardless of license_sub.billing_method —
        an OFFLINE-billed license's school admin can still purchase
        overage via Stripe Checkout here (a customer is lazily
        created/reused via StripeCustomerService.get_or_create_license_
        customer if one doesn't already exist), exactly as for a
        STRIPE-billed license.

        Any caller who is neither of the two above is rejected — this is
        enforced here defensively even though the view's permission class
        (IsSchoolAdminOrSuperAdmin) should already have filtered them out;
        money-moving code must not rely on a single layer of defense.

        Concurrency: a short-lived per-license lock prevents two
        concurrent purchase requests for the same license from both
        passing validation against the same (about-to-become-stale) state.
        Held across the WHOLE call including any Stripe call, mirroring
        IndividualPlanChangeService.select_plan's lock pattern.

        Raises:
            ValueError: for any validation failure, business-rule
                rejection, or Stripe API error (wrapped with a clean
                message).
        """
        lock_key = f"billing:license_overage:{license_sub.id}"
        if not cache.add(
            lock_key,
            "1",
            timeout=LicenseSubscriptionService._OVERAGE_LOCK_TIMEOUT_SECONDS,
        ):
            raise ValueError(
                "An overage purchase is already being processed for this "
                "license. Please wait a moment and try again."
            )

        try:
            # Fresh, UNLOCKED read — deliberately no select_for_update()
            # here. This method only branches and validates; the two
            # branches below take their own tightly-scoped locks
            # immediately before mutating anything, and neither branch
            # ever holds a DB lock across the network call to Stripe.
            license_sub = LicenseSubscription.objects.select_related("plan").get(
                pk=license_sub.pk
            )

            is_super = LicenseSubscriptionService._is_superadmin_actor(requesting_user)
            is_school_admin = LicenseSubscriptionService._is_license_school_admin_actor(
                license_sub, requesting_user
            )

            if not is_super and not is_school_admin:
                raise ValueError(
                    "You are not authorized to purchase overage for this license."
                )

            LicenseSubscriptionService._validate_overage_purchase_request(
                license_sub, allocations, total_blocks
            )

            if is_super:
                return LicenseSubscriptionService._grant_overage_offline(
                    license_sub, requesting_user, total_blocks, allocations
                )

            if payment_method == "offline_request":
                return LicenseSubscriptionService.request_overage_offline(
                    license_sub, requesting_user, total_blocks, allocations
                )

            if not success_url or not cancel_url:
                raise ValueError(
                    "success_url and cancel_url are required to start checkout."
                )

            return LicenseSubscriptionService._create_overage_checkout(
                license_sub,
                requesting_user,
                total_blocks,
                allocations,
                success_url,
                cancel_url,
            )
        finally:
            cache.delete(lock_key)

    @staticmethod
    def _grant_overage_blocks(
        block_size: int,
        blocks_by_teacher: dict,
        allocation_by_teacher: dict,
        ledger_type,
        reference_fn,
        metadata_fn,
    ) -> list:
        """
        Shared core of overage credit granting, used by
        _grant_overage_offline (superadmin comp-grant path), the Stripe
        checkout-completed webhook, and approve_overage_offline_request.

        Callers own locking and deciding WHICH allocations are grantable —
        this grants to exactly the allocations it's handed, nothing more:
          - `blocks_by_teacher`: {str(teacher_id): int blocks}, already
            filtered down to whoever should actually be granted.
          - `allocation_by_teacher`: {str(teacher_id): SchoolCreditAllocation},
            already select_for_update()'d by the caller.
          - `reference_fn`/`metadata_fn`: callables(teacher_id_str, blocks)
            -> str / dict, so each call site keeps its own distinct
            CreditLedger wording (Stripe metadata, superadmin-grant
            metadata, offline-approval metadata) without this helper
            knowing about any of those specifics.

        Overage blocks never expire (expires_at=None) — deliberately NOT
        tied to license_sub.billing_cycle_end. A purchased/granted block
        is a standing balance the school/teacher paid for (or was
        comp-granted); consumption order already guarantees it's drawn
        down last, after every free/rollover bucket, so tying it to the
        cycle it happened to be granted in would forfeit paid-for value
        the customer never got a chance to use.

        Must be called from within an existing @transaction.atomic block —
        does not open its own.

        Returns a list of {"teacher_id", "teacher_email", "blocks",
        "credits_granted"} dicts, one per allocation granted.
        """
        granted_details = []

        for teacher_id_str, blocks in blocks_by_teacher.items():
            allocation = allocation_by_teacher[teacher_id_str]
            teacher = allocation.user
            wallet, _ = CreditWallet.objects.get_or_create(user=teacher)
            raw_credits = blocks * block_size

            bucket = CreditBucket.objects.create(
                wallet=wallet,
                bucket_type=CreditBucketType.OVERAGE,
                total_credits=raw_credits,
                used_credits=0,
                expires_at=None,
            )

            CreditWallet.objects.filter(pk=wallet.pk).update(
                overage_blocks_used=F("overage_blocks_used") + blocks
            )

            CreditLedger.objects.create(
                user=teacher,
                bucket=bucket,
                ledger_type=ledger_type,
                amount=raw_credits,
                reference=reference_fn(teacher_id_str, blocks),
                metadata=metadata_fn(teacher_id_str, blocks),
            )

            granted_details.append(
                {
                    "teacher_id": str(teacher.id),
                    "teacher_email": teacher.email,
                    "blocks": blocks,
                    "credits_granted": raw_credits,
                }
            )

        return granted_details

    @staticmethod
    @transaction.atomic
    def _grant_overage_offline(
        license_sub: LicenseSubscription,
        performed_by,
        total_blocks: int,
        allocations: dict,
    ) -> dict:
        """
        Super-admin path: grants overage blocks to teachers immediately,
        with NO Stripe charge — an administrative grant on behalf of the
        school (comp, negotiated deal, invoiced outside Stripe, etc.).

        All-or-nothing: every listed teacher is re-validated under lock
        before ANY bucket is created. Unlike the checkout path (where
        money has already changed hands and partial fulfillment must be
        tolerated), there's no reason to ever half-apply an unpaid,
        purely-administrative grant — if any teacher is no longer valid,
        the whole request is rejected and the caller can retry with a
        corrected allocation.
        """
        license_sub = LicenseSubscription.objects.select_for_update().get(
            pk=license_sub.pk
        )
        plan = license_sub.plan

        teacher_ids = list(allocations.keys())
        locked_allocations = list(
            SchoolCreditAllocation.objects.select_for_update()
            .filter(
                LicenseSubscriptionService._overage_eligible_allocations_q(license_sub),
                license_subscription=license_sub,
                user_id__in=teacher_ids,
                is_active=True,
            )
            .select_related("user")
        )
        found_ids = {str(a.user_id) for a in locked_allocations}
        missing = {str(t) for t in teacher_ids} - found_ids
        if missing:
            raise ValueError(
                f"Teachers not active under this license: {', '.join(sorted(missing))}"
            )

        alloc_by_teacher = {str(a.user_id): a for a in locked_allocations}

        billing_record = LicenseBillingRecord.objects.create(
            license_subscription=license_sub,
            record_type=LicenseBillingRecordType.MANUAL_OVERAGE_GRANT,
            amount_paid_cents=None,
            notes=(
                f"Superadmin overage grant — {total_blocks} block(s) across "
                f"{len(allocations)} teacher(s). No Stripe charge; bill the "
                f"school outside the platform if applicable."
            ),
            performed_by=performed_by,
        )

        granted_details = LicenseSubscriptionService._grant_overage_blocks(
            block_size=plan.overage_block_size,
            blocks_by_teacher=allocations,
            allocation_by_teacher=alloc_by_teacher,
            ledger_type=CreditLedgerType.GRANT,
            reference_fn=lambda teacher_id_str, blocks: (
                f"Superadmin overage grant ({blocks} block(s)) — "
                f"license {license_sub.id}"
            ),
            metadata_fn=lambda teacher_id_str, blocks: {
                "license_id": str(license_sub.id),
                "blocks": blocks,
                "granted_by": performed_by.email,
                "manual": True,
                "purchase_channel": "SUPERADMIN_OFFLINE",
            },
        )

        BillingTransactionService.record(
            source=BillingTransactionSource.LICENSE,
            transaction_type=BillingTransactionType.LICENSE_OFFLINE_MANUAL_OVERAGE_GRANT,
            status=BillingTransactionStatus.MANUAL,
            billing_method=BillingTransactionMethod.OFFLINE,
            amount_cents=0,
            license_subscription=license_sub,
            license_billing_record=billing_record,
            performed_by=performed_by,
            description=(
                f"Superadmin overage grant — "
                f"{total_blocks * plan.display_overage_block_size:,} AI credit(s) "
                f"across {len(allocations)} teacher(s)"
            ),
            occurred_at=timezone.now(),
        )

        logger.info(
            "Superadmin %s granted %d overage block(s) across %d teacher(s) "
            "under license %s (offline, no Stripe charge).",
            performed_by.email,
            total_blocks,
            len(allocations),
            license_sub.id,
        )

        return {
            "action": "granted",
            "total_blocks": total_blocks,
            "allocations": granted_details,
        }

    @staticmethod
    def _create_overage_checkout(
        license_sub: LicenseSubscription,
        requesting_user,
        total_blocks: int,
        allocations: dict,
        success_url: str,
        cancel_url: str,
    ) -> dict:
        """
        School-admin path: creates a Stripe Checkout Session (mode=
        "payment") for total_blocks worth of overage credit. NOTHING is
        granted here — fulfillment happens only in
        StripeWebhookHandler._handle_license_overage_checkout_completed.

        Uses Checkout's own hosted payment collection rather than
        confirming a PaymentIntent against a pre-saved default payment
        method — this means a card does NOT need to already be on file
        (via setup-payment-method) to purchase overage; Checkout collects
        it directly. Passing `customer=` still lets Stripe offer any
        already-saved payment methods for that customer, so returning
        admins aren't forced to re-enter a card every time.

        Deliberately does NOT hold any DB row lock while calling Stripe —
        the LicenseOveragePurchaseIntent row is a plain insert (nothing
        else can reference it until this method returns its id), and nothing
        else is mutated until the webhook fires.
        """

        from billing.stripe_service import StripeCustomerService

        plan = license_sub.plan
        amount_cents = total_blocks * plan.overage_block_price

        customer_id = StripeCustomerService.get_or_create_license_customer(
            license_sub, requesting_user
        )

        intent = LicenseOveragePurchaseIntent.objects.create(
            license_subscription=license_sub,
            initiated_by=requesting_user,
            total_blocks=total_blocks,
            allocations={str(k): int(v) for k, v in allocations.items()},
            block_size_snapshot=plan.overage_block_size,
            unit_price_cents_snapshot=plan.overage_block_price,
            amount_cents=amount_cents,
            status=LicenseOveragePurchaseStatus.PENDING,
        )

        # block_display = plan.overage_block_size // CONVERSION_FACTOR
        try:
            session = stripe.checkout.Session.create(
                customer=customer_id,
                mode="payment",
                line_items=[
                    {
                        "price": plan.stripe_overage_price_id,
                        "quantity": total_blocks,
                    }
                ],
                success_url=success_url,
                cancel_url=cancel_url,
                metadata={
                    "flow": "license_overage_purchase_checkout",
                    "license_id": str(license_sub.id),
                    "intent_id": str(intent.id),
                },
            )
        except stripe.error.StripeError as exc:
            # Nothing was charged. Mark the intent FAILED so it doesn't
            # linger as a phantom PENDING row.
            intent.status = LicenseOveragePurchaseStatus.FAILED
            intent.failure_reason = f"Checkout session creation failed: {exc}"
            intent.save(update_fields=["status", "failure_reason", "updated_at"])
            raise ValueError(
                "Could not start overage checkout: "
                + describe_stripe_error(
                    exc, fallback_message="Please try again in a moment."
                )
            ) from exc

        # The intent_id is already embedded in the Stripe session's
        # metadata regardless of whether this second save succeeds — so
        # even if the process crashes right here, the webhook can still
        # find and fulfill this intent via metadata alone.
        intent.stripe_checkout_session_id = session.id
        intent.save(update_fields=["stripe_checkout_session_id", "updated_at"])

        logger.info(
            "Created license overage checkout session %s for license %s "
            "(intent %s, %d block(s), %d cents), initiated by %s.",
            session.id,
            license_sub.id,
            intent.id,
            total_blocks,
            amount_cents,
            requesting_user.email,
        )

        return {
            "action": "checkout",
            "checkout_url": session.url,
            "checkout_session_id": session.id,
            "intent_id": str(intent.id),
            "total_blocks": total_blocks,
            "amount_cents": amount_cents,
        }

    # ------------------------------------------------------------------
    # Offline (off-app payment) overage requests
    # ------------------------------------------------------------------

    @staticmethod
    @transaction.atomic
    def request_overage_offline(
        license_sub: LicenseSubscription,
        requesting_user,
        total_blocks: int,
        allocations: dict,
    ) -> dict:
        """
        School-admin path (payment_method="offline_request"): records a
        request to purchase overage blocks paid for OUTSIDE Stripe (bank
        transfer, invoice, cash, etc.). Nothing is granted here — a
        superadmin must review and call approve_overage_offline_request
        or reject_overage_offline_request before any credit moves.

        Called from within initiate_overage_purchase, under the SAME
        per-license cache lock already held there. This branch makes no
        network call, so holding that lock across it costs nothing extra
        and keeps the "prevent two concurrent purchase requests for the
        same license from racing" guarantee uniform across all three
        branches (immediate grant / Stripe checkout / offline request).
        """
        plan = license_sub.plan
        amount_cents_quoted = total_blocks * plan.overage_block_price

        request_obj = LicenseOverageOfflineRequest.objects.create(
            license_subscription=license_sub,
            requested_by=requesting_user,
            total_blocks=total_blocks,
            allocations={str(k): int(v) for k, v in allocations.items()},
            block_size_snapshot=plan.overage_block_size,
            unit_price_cents_snapshot=plan.overage_block_price,
            amount_cents_quoted=amount_cents_quoted,
        )

        logger.info(
            "License %s: %s requested %d offline overage block(s) across "
            "%d teacher(s) — request %s awaiting superadmin review.",
            license_sub.id,
            requesting_user.email,
            total_blocks,
            len(allocations),
            request_obj.id,
        )

        LicenseSubscriptionService._notify_super_admins_offline_overage_pending(
            request_obj
        )

        return {
            "action": "offline_request_pending",
            "request_id": str(request_obj.id),
            "total_blocks": total_blocks,
            "amount_cents": amount_cents_quoted,
        }

    @staticmethod
    @transaction.atomic
    def approve_overage_offline_request(
        request_obj: LicenseOverageOfflineRequest,
        performed_by,
        amount_confirmed_cents: int,
        payment_reference: Optional[str] = None,
        payment_method_label: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> LicenseOverageOfflineRequest:
        """
        Superadmin-only. Re-validates the license and every allocated
        teacher's active status UNDER LOCK at approval time — the request
        may have sat pending for days, during which the teacher roster or
        the license itself can have changed — and grants to whoever is
        still valid. Teachers no longer active are SKIPPED and recorded
        in skipped_allocations rather than blocking the whole approval
        (mirrors the Stripe webhook's partial-fulfillment behavior: the
        school has a real financial claim against this specific
        allocation, so an administrative reshuffle of one teacher
        shouldn't hold the rest hostage).
        """
        lock_key = f"billing:license_overage:{request_obj.license_subscription_id}"
        if not cache.add(
            lock_key,
            "1",
            timeout=LicenseSubscriptionService._OVERAGE_LOCK_TIMEOUT_SECONDS,
        ):
            raise ValueError(
                "An overage purchase is already being processed for this "
                "license. Please wait a moment and try again."
            )

        try:
            request_obj = LicenseOverageOfflineRequest.objects.select_for_update().get(
                pk=request_obj.pk
            )
            if request_obj.status != LicenseOverageOfflineRequestStatus.PENDING:
                raise ValueError("This request has already been reviewed.")

            license_sub = LicenseSubscription.objects.select_for_update().get(
                pk=request_obj.license_subscription_id
            )

            if not license_sub.is_active:
                request_obj.status = LicenseOverageOfflineRequestStatus.REJECTED
                request_obj.rejection_reason = (
                    "License is no longer active — cannot grant overage. "
                    "Reconcile any offline payment already received "
                    "outside the platform."
                )
                request_obj.reviewed_by = performed_by
                request_obj.reviewed_at = timezone.now()
                request_obj.save(
                    update_fields=[
                        "status",
                        "rejection_reason",
                        "reviewed_by",
                        "reviewed_at",
                        "updated_at",
                    ]
                )
                logger.error(
                    "Offline overage request %s auto-rejected on approval "
                    "attempt: license %s is no longer active.",
                    request_obj.id,
                    license_sub.id,
                )
                LicenseSubscriptionService._notify_school_admin_offline_overage_rejected(
                    request_obj
                )
                return request_obj

            teacher_ids = list(request_obj.allocations.keys())
            active_allocations = {
                str(a.user_id): a
                for a in SchoolCreditAllocation.objects.select_for_update()
                .filter(
                    LicenseSubscriptionService._overage_eligible_allocations_q(
                        license_sub
                    ),
                    license_subscription=license_sub,
                    user_id__in=teacher_ids,
                    is_active=True,
                )
                .select_related("user")
            }

            skipped = []
            blocks_by_teacher = {}
            for teacher_id_str, blocks in request_obj.allocations.items():
                if teacher_id_str in active_allocations:
                    blocks_by_teacher[teacher_id_str] = blocks
                else:
                    skipped.append({"teacher_id": teacher_id_str, "blocks": blocks})

            fulfilled = LicenseSubscriptionService._grant_overage_blocks(
                block_size=request_obj.block_size_snapshot,
                blocks_by_teacher=blocks_by_teacher,
                allocation_by_teacher=active_allocations,
                ledger_type=CreditLedgerType.PURCHASE,
                reference_fn=lambda teacher_id_str, blocks: (
                    f"Offline overage request approved — "
                    f"{payment_method_label or 'off-app payment'}"
                ),
                metadata_fn=lambda teacher_id_str, blocks: {
                    "license_id": str(license_sub.id),
                    "request_id": str(request_obj.id),
                    "requested_by": (
                        request_obj.requested_by.email
                        if request_obj.requested_by
                        else None
                    ),
                    "approved_by": performed_by.email,
                    "blocks_purchased": blocks,
                    "payment_reference": payment_reference,
                    "payment_method_label": payment_method_label,
                },
            )

            if not fulfilled:
                logger.error(
                    "Offline overage request %s approved but EVERY "
                    "allocated teacher was no longer active — 0/%d "
                    "granted. Needs manual follow-up. License %s.",
                    request_obj.id,
                    len(request_obj.allocations),
                    license_sub.id,
                )
            elif skipped:
                logger.error(
                    "Offline overage request %s PARTIALLY fulfilled on "
                    "approval: %d/%d teachers granted, %d skipped (no "
                    "longer active). License %s.",
                    request_obj.id,
                    len(fulfilled),
                    len(request_obj.allocations),
                    len(skipped),
                    license_sub.id,
                )

            request_obj.status = LicenseOverageOfflineRequestStatus.APPROVED
            request_obj.amount_confirmed_cents = amount_confirmed_cents
            request_obj.payment_reference = payment_reference
            request_obj.payment_method_label = payment_method_label
            request_obj.fulfilled_allocations = fulfilled
            request_obj.skipped_allocations = skipped
            request_obj.reviewed_by = performed_by
            request_obj.reviewed_at = timezone.now()

            billing_record = LicenseBillingRecord.objects.create(
                license_subscription=license_sub,
                record_type=LicenseBillingRecordType.OFFLINE_OVERAGE_REQUEST_APPROVED,
                amount_paid_cents=amount_confirmed_cents,
                payment_reference=payment_reference,
                payment_method_label=payment_method_label,
                notes=notes,
                performed_by=performed_by,
            )
            request_obj.license_billing_record = billing_record
            request_obj.save(
                update_fields=[
                    "status",
                    "amount_confirmed_cents",
                    "payment_reference",
                    "payment_method_label",
                    "fulfilled_allocations",
                    "skipped_allocations",
                    "reviewed_by",
                    "reviewed_at",
                    "license_billing_record",
                    "updated_at",
                ]
            )

            BillingTransactionService.record(
                source=BillingTransactionSource.LICENSE,
                transaction_type=BillingTransactionType.LICENSE_OFFLINE_OVERAGE_PURCHASE,
                status=BillingTransactionStatus.PAID,
                billing_method=BillingTransactionMethod.OFFLINE,
                amount_cents=amount_confirmed_cents,
                license_subscription=license_sub,
                license_billing_record=billing_record,
                performed_by=performed_by,
                description=(
                    f"Offline overage request approved — "
                    f"{request_obj.total_blocks * (request_obj.block_size_snapshot // CONVERSION_FACTOR):,} "
                    f"AI credit(s) across {len(request_obj.allocations)} teacher(s)"
                    + (f" ({len(skipped)} skipped, needs review)" if skipped else "")
                ),
                occurred_at=timezone.now(),
            )

            logger.info(
                "Offline overage request %s approved by %s: %d block(s) "
                "granted across %d/%d teacher(s) for license %s.",
                request_obj.id,
                performed_by.email,
                request_obj.total_blocks,
                len(fulfilled),
                len(request_obj.allocations),
                license_sub.id,
            )

            LicenseSubscriptionService._notify_school_admin_offline_overage_approved(
                request_obj
            )

            return request_obj
        finally:
            cache.delete(lock_key)

    @staticmethod
    @transaction.atomic
    def reject_overage_offline_request(
        request_obj: LicenseOverageOfflineRequest,
        performed_by,
        rejection_reason: str,
    ) -> LicenseOverageOfflineRequest:
        """
        Superadmin-only. No credits are touched and no billing record is
        created — nothing financial happened. The select_for_update() +
        status guard alone is enough to prevent a double-reject or a
        reject-after-approve race, since only approval ever grants.
        """
        request_obj = LicenseOverageOfflineRequest.objects.select_for_update().get(
            pk=request_obj.pk
        )
        if request_obj.status != LicenseOverageOfflineRequestStatus.PENDING:
            raise ValueError("This request has already been reviewed.")

        request_obj.status = LicenseOverageOfflineRequestStatus.REJECTED
        request_obj.rejection_reason = rejection_reason
        request_obj.reviewed_by = performed_by
        request_obj.reviewed_at = timezone.now()
        request_obj.save(
            update_fields=[
                "status",
                "rejection_reason",
                "reviewed_by",
                "reviewed_at",
                "updated_at",
            ]
        )

        logger.info(
            "Offline overage request %s rejected by %s: %s",
            request_obj.id,
            performed_by.email,
            rejection_reason,
        )

        LicenseSubscriptionService._notify_school_admin_offline_overage_rejected(
            request_obj
        )

        return request_obj

    @staticmethod
    def _notify_super_admins_offline_overage_pending(
        request_obj: LicenseOverageOfflineRequest,
    ) -> None:
        """
        Best-effort — must never raise out of request_overage_offline.
        Money-related, so this is NOT gated behind any per-user
        notification preference; fires to every active super admin.
        """
        license_sub = request_obj.license_subscription
        school = license_sub.school
        requested_by = request_obj.requested_by

        teacher_breakdown = (
            LicenseSubscriptionService._build_offline_overage_teacher_breakdown(
                request_obj
            )
        )

        context = {
            "school": school,
            "license_subscription": license_sub,
            "requested_by": requested_by,
            "total_blocks": request_obj.total_blocks,
            "amount_cents_quoted": request_obj.amount_cents_quoted,
            "amount_quoted_display": f"{request_obj.amount_cents_quoted / 100:.2f}",
            "teacher_breakdown": teacher_breakdown,
            "request_id": str(request_obj.id),
            "current_year": timezone.now().year,
            "support_email": settings.SUPPORT_EMAIL,
        }

        def _dispatch():
            recipients = list(
                CustomUser.objects.filter(
                    user_type=UserTypes.SUPER_ADMIN,
                    is_superuser=True,
                    is_active=True,
                    email__isnull=False,
                ).exclude(email="")
            )
            for admin in recipients:
                try:
                    html_message = render_to_string(
                        "email/license_overage_offline_request_pending.html",
                        context={**context, "admin": admin},
                    )
                    send_email_task.delay(
                        subject=f"New offline overage request pending — {school.name}",
                        message=(
                            f"{school.name} has requested {request_obj.total_blocks} "
                            "offline overage block(s). Review it in the admin dashboard."
                        ),
                        from_email=settings.DEFAULT_FROM_EMAIL,
                        recipient_list=[admin.email],
                        html_message=html_message,
                    )
                except Exception:
                    logger.exception(
                        "Failed to queue offline-overage-pending email to "
                        "super admin %s for request %s.",
                        admin.email,
                        request_obj.id,
                    )

        transaction.on_commit(_dispatch)

    @staticmethod
    def _notify_school_admin_offline_overage_approved(
        request_obj: LicenseOverageOfflineRequest,
    ) -> None:
        """Best-effort — must never raise out of approve_overage_offline_request."""
        license_sub = request_obj.license_subscription
        school = license_sub.school
        requested_by = request_obj.requested_by

        context = {
            "school": school,
            "total_blocks": request_obj.total_blocks,
            "amount_confirmed_cents": request_obj.amount_confirmed_cents,
            "amount_confirmed_display": f"{request_obj.amount_confirmed_cents / 100:.2f}",
            "payment_reference": request_obj.payment_reference,
            "fulfilled_allocations": request_obj.fulfilled_allocations or [],
            "skipped_allocations": request_obj.skipped_allocations or [],
            "reviewed_by": request_obj.reviewed_by,
            "current_year": timezone.now().year,
            "support_email": settings.SUPPORT_EMAIL,
        }
        recipient_email = requested_by.email if requested_by else None

        def _dispatch():
            if not recipient_email:
                return
            try:
                html_message = render_to_string(
                    "email/license_overage_offline_request_approved.html",
                    context=context,
                )
                send_email_task.delay(
                    subject=f"Your overage request has been approved — {school.name}",
                    message=(
                        f"Your offline overage request for {request_obj.total_blocks} "
                        "block(s) has been approved."
                    ),
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[recipient_email],
                    html_message=html_message,
                )
            except Exception:
                logger.exception(
                    "Failed to queue offline-overage-approved email to %s "
                    "for request %s.",
                    recipient_email,
                    request_obj.id,
                )

        transaction.on_commit(_dispatch)

    @staticmethod
    def _notify_school_admin_offline_overage_rejected(
        request_obj: LicenseOverageOfflineRequest,
    ) -> None:
        """Best-effort — must never raise out of the caller."""
        license_sub = request_obj.license_subscription
        school = license_sub.school
        requested_by = request_obj.requested_by

        context = {
            "school": school,
            "total_blocks": request_obj.total_blocks,
            "rejection_reason": request_obj.rejection_reason,
            "reviewed_by": request_obj.reviewed_by,
            "current_year": timezone.now().year,
            "support_email": settings.SUPPORT_EMAIL,
        }
        recipient_email = requested_by.email if requested_by else None

        def _dispatch():
            if not recipient_email:
                return
            try:
                html_message = render_to_string(
                    "email/license_overage_offline_request_rejected.html",
                    context=context,
                )
                send_email_task.delay(
                    subject=f"Your overage request was not approved — {school.name}",
                    message=(
                        f"Your offline overage request for {request_obj.total_blocks} "
                        f"block(s) was not approved: {request_obj.rejection_reason}"
                    ),
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[recipient_email],
                    html_message=html_message,
                )
            except Exception:
                logger.exception(
                    "Failed to queue offline-overage-rejected email to %s "
                    "for request %s.",
                    recipient_email,
                    request_obj.id,
                )

        transaction.on_commit(_dispatch)

    @staticmethod
    def _build_offline_overage_teacher_breakdown(
        request_obj: LicenseOverageOfflineRequest,
    ) -> list:
        """
        Per-teacher breakdown for both the review-table serializer and
        the pending-request email — includes a LIVE is_currently_active
        flag (not cached) since the roster can drift while a request
        sits pending. Also includes `user_type` so a reviewer can tell a
        regular teacher allocation apart from the license admin's own
        analytics allocation (SCHOOL_ADMIN) when the admin has bought
        overage for themselves.
        """
        teacher_ids = list(request_obj.allocations.keys())
        allocations_by_teacher = {
            str(a.user_id): a
            for a in SchoolCreditAllocation.objects.filter(
                license_subscription_id=request_obj.license_subscription_id,
                user_id__in=teacher_ids,
            ).select_related("user")
        }

        breakdown = []
        for teacher_id_str, blocks in request_obj.allocations.items():
            allocation = allocations_by_teacher.get(teacher_id_str)
            is_currently_active = bool(allocation and allocation.is_active)
            teacher = allocation.user if allocation else None
            breakdown.append(
                {
                    "teacher_id": teacher_id_str,
                    "teacher_email": teacher.email if teacher else None,
                    "teacher_name": teacher.get_full_name() if teacher else None,
                    "user_type": teacher.user_type if teacher else None,
                    "blocks": blocks,
                    "credits": blocks * request_obj.block_size_snapshot,
                    "is_currently_active": is_currently_active,
                }
            )
        return breakdown

    @staticmethod
    @transaction.atomic
    def _refresh_teacher_credits(allocation: SchoolCreditAllocation) -> None:
        """
        Refresh a teacher's monthly credits: expire current monthly bucket,
        apply rollover, and create a new monthly bucket.
        Called by the monthly refresh task.
        """

        teacher = allocation.user
        wallet = teacher.credit_wallet
        license_sub = allocation.license_subscription
        # plan = license_sub.plan
        now = timezone.now()
        next_refresh = now + relativedelta(months=1)

        LicenseSubscriptionService._rollover_and_grant_monthly_bucket(
            teacher=teacher,
            wallet=wallet,
            plan=license_sub.plan,
            grant_amount=allocation.monthly_allocation,
            new_expiry=next_refresh,
            now=now,
            reference=f"Monthly grant for license {license_sub.id}",
            metadata={
                "license_id": str(license_sub.id),
                "allocation_id": str(allocation.id),
                "refresh_month": now.strftime("%Y-%m"),
            },
        )
        # 3. Update allocation's next_credit_grant_at
        allocation.next_credit_grant_at = next_refresh
        allocation.save(update_fields=["next_credit_grant_at", "updated_at"])

        logger.info(
            "Refreshed monthly credits for teacher %s under license %s. Amount: %d, next refresh: %s",
            teacher.email,
            license_sub.id,
            next_refresh,
        )

    @staticmethod
    @transaction.atomic
    def process_offline_renewal(
        license_sub: LicenseSubscription,
        performed_by: CustomUser,
        new_billing_cycle_end,
        amount_paid_cents: Optional[int] = None,
        payment_reference: Optional[str] = None,
        payment_method_label: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> LicenseSubscription:
        """
        Superadmin-triggered renewal for an OFFLINE-billed license. No
        idempotency early-return by design — unlike process_license_renewal
        (which must not double-fire off a Stripe webhook + Celery race),
        this is a deliberate human action and the superadmin may legitimately
        renew early (school paid ahead) or "late" relative to the old cycle
        end (paperwork lag). The row lock below only protects against a
        genuine accidental double-click, not against intentional re-renewal.
        """

        if license_sub.billing_method != LicenseBillingMethod.OFFLINE:
            raise ValueError(
                f"License {license_sub.id} is billed via {license_sub.billing_method}, "
                "not OFFLINE. Use the Stripe renewal path instead."
            )

        license_sub = LicenseSubscription.objects.select_for_update().get(
            pk=license_sub.pk
        )

        if not license_sub.is_active:
            raise ValueError("Cannot renew an inactive license. Reactivate it first.")

        now = timezone.now()
        if new_billing_cycle_end <= now:
            raise ValueError("new_billing_cycle_end must be in the future.")

        previous_cycle_end = license_sub.billing_cycle_end

        active_allocations = list(
            license_sub.allocations.filter(is_active=True).select_related(
                "user__credit_wallet"
            )
        )

        renewed_count = 0
        failed_teachers = []

        for allocation in active_allocations:
            with transaction.atomic():
                try:
                    teacher = allocation.user
                    wallet = teacher.credit_wallet

                    LicenseSubscriptionService._rollover_and_grant_monthly_bucket(
                        teacher=teacher,
                        wallet=wallet,
                        plan=license_sub.plan,
                        grant_amount=allocation.monthly_allocation,
                        new_expiry=now + relativedelta(months=1),
                        now=now,
                        reference=f"Offline renewal allocation for license {license_sub.id}",
                        metadata={
                            "license_subscription_id": str(license_sub.id),
                            "allocation_id": str(allocation.id),
                            "renewal_type": "OFFLINE",
                        },
                    )

                    allocation.next_credit_grant_at = now + relativedelta(months=1)
                    allocation.save(
                        update_fields=["next_credit_grant_at", "updated_at"]
                    )

                    wallet.overage_blocks_used = 0
                    wallet.save(update_fields=["overage_blocks_used", "updated_at"])

                    renewed_count += 1
                except Exception as e:
                    logger.error(
                        "Offline renewal: failed to refresh credits for teacher %s "
                        "under license %s: %s",
                        allocation.user.email,
                        license_sub.id,
                        str(e),
                    )
                    failed_teachers.append(allocation.user.email)

        license_sub.billing_cycle_start = now
        license_sub.billing_cycle_end = new_billing_cycle_end
        license_sub.total_credits_consumed = 0
        license_sub.save(
            update_fields=[
                "billing_cycle_start",
                "billing_cycle_end",
                "total_credits_consumed",
                "updated_at",
            ]
        )

        billing_record = LicenseBillingRecord.objects.create(
            license_subscription=license_sub,
            record_type=LicenseBillingRecordType.RENEWED_OFFLINE,
            amount_paid_cents=amount_paid_cents,
            payment_reference=payment_reference,
            payment_method_label=payment_method_label,
            notes=notes,
            previous_billing_cycle_end=previous_cycle_end,
            new_billing_cycle_end=new_billing_cycle_end,
            performed_by=performed_by,
        )

        BillingTransactionService.record(
            source=BillingTransactionSource.LICENSE,
            transaction_type=BillingTransactionType.LICENSE_OFFLINE_RENEWAL,
            status=BillingTransactionStatus.MANUAL,
            billing_method=BillingTransactionMethod.OFFLINE,
            amount_cents=amount_paid_cents or 0,
            license_subscription=license_sub,
            license_billing_record=billing_record,
            performed_by=performed_by,
            description=notes
            or f"Offline renewal — cycle extended to {new_billing_cycle_end.date().isoformat()}",
            occurred_at=timezone.now(),
        )

        logger.info(
            "Offline renewal for license %s by %s: %d teacher(s) refreshed, "
            "%d failed. Cycle: %s -> %s.",
            license_sub.id,
            performed_by.email if performed_by else "unknown",
            renewed_count,
            len(failed_teachers),
            previous_cycle_end,
            new_billing_cycle_end,
        )

        return license_sub

    @staticmethod
    @transaction.atomic
    def convert_license_to_offline(
        license_sub: LicenseSubscription,
        performed_by: CustomUser,
        notes: Optional[str] = None,
    ) -> LicenseSubscription:

        license_sub = LicenseSubscription.objects.select_for_update().get(
            pk=license_sub.pk
        )

        if license_sub.billing_method == LicenseBillingMethod.OFFLINE:
            raise ValueError("License is already billed offline.")

        if license_sub.stripe_subscription_id:
            try:
                stripe.Subscription.delete(license_sub.stripe_subscription_id)
            except stripe.error.StripeError as exc:
                raise ValueError(
                    f"Failed to cancel Stripe subscription: {exc}"
                ) from exc

        license_sub.billing_method = LicenseBillingMethod.OFFLINE
        license_sub.stripe_subscription_id = None
        license_sub.stripe_status = None
        license_sub.save(
            update_fields=[
                "billing_method",
                "stripe_subscription_id",
                "stripe_status",
                "updated_at",
            ]
        )

        LicenseBillingRecord.objects.create(
            license_subscription=license_sub,
            record_type=LicenseBillingRecordType.CONVERTED_TO_OFFLINE,
            notes=notes,
            performed_by=performed_by,
        )

        logger.info(
            "License %s converted from STRIPE to OFFLINE billing by %s.",
            license_sub.id,
            performed_by.email if performed_by else "unknown",
        )

        return license_sub

    @staticmethod
    @transaction.atomic
    def grant_manual_teacher_overage(
        license_sub: LicenseSubscription,
        teacher: CustomUser,
        blocks: int,
        performed_by: CustomUser,
        amount_paid_cents: Optional[int] = None,
        payment_reference: Optional[str] = None,
        payment_method_label: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> CreditBucket:
        if blocks <= 0:
            raise ValueError("blocks must be a positive integer.")

        allocation = (
            SchoolCreditAllocation.objects.select_for_update()
            .filter(license_subscription=license_sub, user=teacher, is_active=True)
            .first()
        )
        if not allocation:
            raise ValueError(
                f"{teacher.email} is not actively enrolled under this license."
            )

        plan = license_sub.plan
        wallet, _ = CreditWallet.objects.get_or_create(user=teacher)
        raw_credits = blocks * plan.overage_block_size

        # Overage never expires (expires_at=None) — the same invariant as
        # every other overage grant path (_grant_overage_blocks,
        # SubscriptionService.grant_overage_bucket, and the
        # test_overage_never_expires suite). This path used to set
        # expires_at=license_sub.billing_cycle_end, silently expiring
        # paid-for blocks at cycle end.
        bucket = CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.OVERAGE,
            total_credits=raw_credits,
            used_credits=0,
            expires_at=None,
        )

        CreditWallet.objects.filter(pk=wallet.pk).update(
            overage_blocks_used=F("overage_blocks_used") + blocks
        )

        CreditLedger.objects.create(
            user=teacher,
            bucket=bucket,
            ledger_type=CreditLedgerType.GRANT,
            amount=raw_credits,
            reference=f"Manual overage grant ({blocks} block(s)) — license {license_sub.id}",
            metadata={
                "license_id": str(license_sub.id),
                "blocks": blocks,
                "granted_by": performed_by.email if performed_by else None,
                "manual": True,
            },
        )

        billing_record = LicenseBillingRecord.objects.create(
            license_subscription=license_sub,
            record_type=LicenseBillingRecordType.MANUAL_OVERAGE_GRANT,
            amount_paid_cents=amount_paid_cents,
            payment_reference=payment_reference,
            payment_method_label=payment_method_label,
            notes=notes
            or f"{blocks} overage block(s) manually granted to {teacher.email}.",
            performed_by=performed_by,
        )

        BillingTransactionService.record(
            source=BillingTransactionSource.LICENSE,
            transaction_type=BillingTransactionType.LICENSE_OFFLINE_MANUAL_OVERAGE_GRANT,
            status=(
                BillingTransactionStatus.PAID
                if amount_paid_cents
                else BillingTransactionStatus.MANUAL
            ),
            billing_method=license_sub.billing_method,
            amount_cents=amount_paid_cents or 0,
            license_subscription=license_sub,
            license_billing_record=billing_record,
            performed_by=performed_by,
            description=notes
            or (
                f"Manual overage grant — {raw_credits // CONVERSION_FACTOR:,} "
                f"AI credit(s) to {teacher.email}"
            ),
            occurred_at=timezone.now(),
        )

        logger.info(
            "Manually granted %d overage block(s) (%d raw credits) to %s under "
            "license %s by %s.",
            blocks,
            raw_credits,
            teacher.email,
            license_sub.id,
            performed_by.email if performed_by else "unknown",
        )

        return bucket

    @staticmethod
    def select_plan(
        license_sub,
        new_plan,
        custom_price_cents=None,
        remove_custom_price=False,
        performed_by=None,
    ):
        license_sub = LicenseSubscription.objects.select_related("plan").get(
            pk=license_sub.pk
        )

        if not license_sub.is_active:
            raise ValueError(
                "Cannot change the plan of an inactive license. Reactivate it first"
            )

        if new_plan.category != PlanCategory.LICENSE:
            raise ValueError(
                f"License subscriptions require a LICENSE plan, not "
                f"{new_plan.category}."
            )

        if not new_plan.is_active:
            raise ValueError("This plan is no longer available for selection.")

        if (
            license_sub.billing_method == LicenseBillingMethod.STRIPE
            and license_sub.stripe_status == StripeSubscriptionStatus.PAST_DUE
        ):
            raise ValueError(
                "This license has a payment issue. Please resolve it (or "
                "convert it to offline billing) before changing plans."
            )

        old_price, new_price, _ = LicenseSubscriptionService._resolve_effective_price(
            license_sub, new_plan, custom_price_cents, remove_custom_price
        )

        updated_license = LicenseSubscriptionService.change_license_plan(
            license_sub,
            new_plan,
            custom_price_cents=custom_price_cents,
            remove_custom_price=remove_custom_price,
            performed_by=performed_by,
        )

        display_name = new_plan.display_name or new_plan.name

        if license_sub.billing_method == LicenseBillingMethod.OFFLINE:
            action = "recorded_offline"
            message = (
                f"License moved to {display_name}. This license is billed "
                f"offline, so no Stripe charge was made. A billing record "
                f"was logged — remember to adjust the school's invoice or "
                f"contract to match the new price separately."
            )

        elif new_price > old_price:
            action = "charged"
            message = (
                f"License upgraded to {display_name}. The school was "
                f"charged the prorated difference immediately."
            )
        elif new_price < old_price:
            action = "changed_deferred_billing"
            message = (
                f"License moved to {display_name}. Teacher allocations were "
                f"updated immediately, but the lower price won't be "
                f"reflected on Stripe's bill until the next invoice — no "
                f"refund is issued for the current cycle."
            )
        else:
            action = "changed_no_billing_impact"
            message = (
                f"License moved to {display_name}. The price is unchanged, "
                f"so no Stripe charge or billing adjustment was needed — "
                f"only the plan and teacher allocations were updated."
            )

        logger.info(
            "License %s plan change resolved to action=%s (%s -> %s, "
            "billing_method=%s, old_price=%s, new_price=%s).",
            license_sub.id,
            action,
            license_sub.plan.name,
            new_plan.name,
            license_sub.billing_method,
            old_price,
            new_price,
        )

        return {"action": action, "message": message, "license": updated_license}
