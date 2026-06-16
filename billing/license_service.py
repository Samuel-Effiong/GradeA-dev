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
from typing import List, Optional

from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.db import transaction

# from django.db.models import F, Q
from django.utils import timezone

from AutoGrader.tasks import send_email_task
from classrooms.models import School
from users.models import CustomUser, RegistrationMethod, UserTypes
from users.services import otp_manager
from users.utils import is_business_email

from .models import (  # CONVERSION_FACTOR,; UserSubscription,
    CreditBucket,
    CreditBucketType,
    CreditLedger,
    CreditLedgerType,
    CreditWallet,
    LicenseSubscription,
    PlanCategory,
    PlanTier,
    SchoolCreditAllocation,
    SubscriptionPlan,
)

logger = logging.getLogger(__name__)


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
    @transaction.atomic
    def create_license_subscription(
        school: School,
        plan: SubscriptionPlan,
        admin_user: CustomUser,
        teacher_emails: Optional[List[str]] = None,
        contract_months: int = 12,
        max_seats: int = 0,
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

        Returns:
            LicenseSubscription: The newly created license

        Raises:
            ValueError: If plan or admin validation fails, or contract_months is invalid
            School.DoesNotExist: If school doesn't exist
        """
        # 1. Validate inputs
        LicenseSubscriptionService.validate_license_plan(plan)
        LicenseSubscriptionService.validate_admin_user(admin_user, school)

        if contract_months not in (1, 9, 10, 12):
            raise ValueError(
                f"contract_months must be 9, 10, or 12. Got: {contract_months}"
            )

        if max_seats < 0:
            raise ValueError("max_seats must be 0 (unlimited) or a positive integer.")

        # Validate that initial teachers emails don't exceed the seat cap
        if max_seats > 0 and teacher_emails and len(teacher_emails) > max_seats:
            raise ValueError(
                f"Cannot enroll {len(teacher_emails)} teachers: license max_seats is {max_seats}."
            )

        now = timezone.now()
        # Use contract_months to compute the billing window (e.g. 12 months for annual)
        billing_end = now + relativedelta(months=contract_months)

        # 2. Check for existing active license (only one per school)
        existing_license = LicenseSubscription.objects.filter(
            school=school, is_active=True
        ).first()

        if existing_license:
            logger.warning(
                "School %s already has active license subscription %s. "
                "Deactivating old license before creating new one.",
                school.id,
                existing_license.id,
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
            auto_renew=True,
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

        # 4. Enroll teachers if provided
        if teacher_emails:
            for email in teacher_emails:
                try:
                    teacher = LicenseSubscriptionService._get_or_invite_teacher(
                        email, school, admin_user, raise_on_conflict=True
                    )
                    LicenseSubscriptionService._enroll_teacher_internal(
                        license_sub, teacher
                    )
                except CustomUser.DoesNotExist:
                    logger.error(
                        "Teacher with ID %s not found. Skipping enrollment "
                        "in license %s.",
                        email,
                        license_sub.id,
                    )
                except Exception as e:
                    logger.error(
                        "Failed to enroll teacher %s in license %s: %s",
                        email,
                        license_sub.id,
                        str(e),
                    )

        logger.info(
            "LicenseSubscription %s creation complete. " "Enrolled %d teachers.",
            license_sub.id,
            license_sub.teacher_count,
        )

        return license_sub

    @staticmethod
    def _get_or_invite_teacher(
        email: str,
        school: School,
        admin_user: CustomUser,
        raise_on_conflict: bool = False,
    ) -> CustomUser:
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
        if not is_business_email(email):
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

            # 3. Check for active individua subscription
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

            # 5. If user exists but inactive, ensure that they have a valid activation token
            if not user.is_active:
                if (
                    not user.activation_token
                    or user.activation_expires < timezone.now()
                ):
                    user.activation_token = otp_manager.generate_otp()
                    user.activation_expires = timezone.now() + timedelta(days=7)
                    user.save(update_fields=["activation_token", "activation_expires"])

                    # Re-send invitation email
                    LicenseSubscriptionService._send_teacher_invitation(
                        user, school, admin_user
                    )

            return user

        # Create new teacher account (inactive)
        activation_token = otp_manager.generate_otp()
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

        # Send invitation email
        LicenseSubscriptionService._send_teacher_invitation(user, school, admin_user)
        return user

    @staticmethod
    def _send_teacher_invitation(
        teacher: CustomUser, school: School, admin_user: CustomUser
    ):
        """
        Send activation email to a newly invited teacher
        """

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
        send_email_task.delay(
            subject=f"Invitation to teach at {school.name}",
            message="",
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[teacher.email],
            html_message=None,
            template_id="ynrw7gy0ye2l2k8e",  # reuse student template or create new one
            merge_data=merge_data,
        )

    @staticmethod
    def _enroll_teacher_internal(
        license_sub: LicenseSubscription, teacher: CustomUser
    ) -> SchoolCreditAllocation:
        """
        Internal method to enroll a single teacher in a license.

        Handles:
        1. Creating SchoolCreditAllocation
        2. Ensuring CreditWallet exists
        3. Creating MONTHLY bucket
        4. Deactivating conflicting INDIVIDUAL subscriptions
        5. Audit logging

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

        # 3. Ensure teacher's CreditWallet exists
        wallet, wallet_created = CreditWallet.objects.get_or_create(user=teacher)
        if wallet_created:
            logger.info("Created CreditWallet for teacher %s", teacher.email)

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
                # Apply rollover rules from the new LICENSE plan
                rollover_amount = min(
                    int(unused * (license_sub.plan.carry_over_percent / 100)),
                    license_sub.plan.carry_over_max,
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
                        },
                    )
                    logger.info(
                        "Carried over %d credits for teacher %s when "
                        "transitioning to license",
                        rollover_amount,
                        teacher.email,
                    )

            # Expire the old bucket
            existing_monthly.expires_at = now
            existing_monthly.save(update_fields=["expires_at", "updated_at"])
            logger.info(
                "Expired old MONTHLY bucket for teacher %s",
                teacher.email,
            )

        # 6. Create new MONTHLY bucket for the license allocation
        monthly_bucket = CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=allocation.monthly_allocation,
            used_credits=0,
            expires_at=license_sub.billing_cycle_end,
        )

        # 7. Create audit ledger entry
        CreditLedger.objects.create(
            user=teacher,
            bucket=monthly_bucket,
            ledger_type=CreditLedgerType.GRANT,
            amount=allocation.monthly_allocation,
            reference=(
                f"Initial allocation for LICENSE subscription {license_sub.id} "
                f"({license_sub.plan.display_name or license_sub.plan.name})"
            ),
            metadata={
                "license_subscription_id": str(license_sub.id),
                "school_id": str(license_sub.school.id),
                "allocation_id": str(allocation.id),
                "teacher_email": teacher.email,
            },
        )

        logger.info(
            "Created MONTHLY credit bucket with %d credits for teacher %s "
            "under license %s",
            allocation.monthly_allocation,
            teacher.email,
            license_sub.id,
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

        results = {"successful": 0, "failed": 0, "errors": []}

        for email in teacher_emails:
            try:
                teacher = LicenseSubscriptionService._get_or_invite_teacher(
                    email,
                    license_sub.school,
                    license_sub.admin_user,
                    raise_on_conflict=False,  # Do not raise, return None on conflict
                )

                if teacher is None:
                    # Confict aready logged inside _get_or_invite_teacher
                    results["failed"] += 1
                    results["errors"].append(
                        {
                            "teacher_email": email,
                            "error": "Individual subscription conflict or invalid email domain.",
                        }
                    )
                    continue

                # Enroll the teacher
                LicenseSubscriptionService._enroll_teacher_internal(
                    license_sub, teacher
                )
                results["successful"] += 1

            except Exception as e:
                results["failed"] += 1
                results["errors"].append(
                    {
                        "teacher_email": email,
                        "error": str(e),
                    }
                )
                logger.error("Failed to add teacher %s to license: %s", email, str(e))

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

        The allocation is marked inactive but not deleted (for audit trail).

        Args:
            license_sub: License subscription
            teacher: Teacher to remove

        Raises:
            ValueError: If allocation doesn't exist
        """
        allocation = SchoolCreditAllocation.objects.filter(
            license_subscription=license_sub,
            user=teacher,
            is_active=True,
        ).first()

        if not allocation:
            raise ValueError(
                f"Teacher {teacher.email} is not actively enrolled in "
                f"license {license_sub.id}"
            )

        allocation.is_active = False
        allocation.save(update_fields=["is_active", "updated_at"])

        logger.info(
            "Removed teacher %s from license %s",
            teacher.email,
            license_sub.id,
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

                    # 1. Get the current MONTHLY bucket
                    old_monthly = wallet.buckets.filter(
                        bucket_type=CreditBucketType.MONTHLY,
                        expires_at__lte=now,  # Should be expired by now
                    ).first()

                    if old_monthly:
                        unused = old_monthly.remaining_credits

                        if unused > 0:
                            # Apply rollover
                            rollover_amount = min(
                                int(
                                    unused * (license_sub.plan.carry_over_percent / 100)
                                ),
                                license_sub.plan.carry_over_max,
                            )

                            if rollover_amount > 0:
                                expiry = renewal_start + relativedelta(
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
                                        f"Rollover from LICENSE cycle renewal "
                                        f"(license {license_sub.id})"
                                    ),
                                    metadata={
                                        "previous_unused": unused,
                                        "rollover_percent": str(
                                            license_sub.plan.carry_over_percent
                                        ),
                                        "license_id": str(license_sub.id),
                                    },
                                )

                        # Expire the old bucket
                        old_monthly.expires_at = renewal_start
                        old_monthly.save(update_fields=["expires_at", "updated_at"])

                    # 2. Create new MONTHLY bucket using the allocation's monthly_allocation
                    # (already updated by update_license_plan if plan changed)
                    new_monthly = CreditBucket.objects.create(
                        wallet=wallet,
                        bucket_type=CreditBucketType.MONTHLY,
                        total_credits=allocation.monthly_allocation,
                        used_credits=0,
                        expires_at=renewal_end,
                    )

                    CreditLedger.objects.create(
                        user=teacher,
                        bucket=new_monthly,
                        ledger_type=CreditLedgerType.GRANT,
                        amount=allocation.monthly_allocation,
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

                    # 3. Reset overage blocks
                    wallet.overage_blocks_used = 0
                    wallet.save(update_fields=["overage_blocks_used", "updated_at"])

                    renewal_count += 1

                except Exception as e:
                    # Log the error but do not raise – allow other teachers to renew
                    logger.error(
                        "Failed to renew credits for teacher %s under license %s: %s",
                        allocation.user.email,
                        license_sub.id,
                        str(e),
                    )
                    failed_teachers.append(allocation.user.email)
                    # The inner transaction.atomic() will rollback only this teacher's changes

        # 4. Update license cycle dates only if at least one teacher renewed successfully
        # (or you may choose to update even if all failed, but that would be odd)
        if renewal_count > 0 or not active_allocations:
            license_sub.billing_cycle_start = renewal_start
            license_sub.billing_cycle_end = renewal_end
            license_sub.save(
                update_fields=["billing_cycle_start", "billing_cycle_end", "updated_at"]
            )
        else:
            # No teacher could be renewed – deactivate the license to avoid endless retries
            logger.error(
                "License %s renewal failed for all %d teachers. Deactivating license.",
                license_sub.id,
                len(active_allocations),
            )
            license_sub.is_active = False
            license_sub.save(update_fields=["is_active", "updated_at"])
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
        active_allocations = license_sub.allocations.filter(is_active=True)

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
