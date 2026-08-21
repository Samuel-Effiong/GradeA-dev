"""
billing/stripe_view_schemas.py
================================
drf-spectacular @extend_schema decorators for every Stripe-related view
added to SubscriptionManagementViewSet and LicenseSubscriptionViewSet.

HOW TO USE
----------
These are drop-in replacements for the @extend_schema decorators on the
affected action methods. Copy the decorator + method signature block for
each action into the corresponding ViewSet in views.py / license_views.py.

The file is also importable — you can factor the OpenApiResponse /
inline_serializer objects out and reference them from within the ViewSet
files directly if you prefer to keep schemas co-located with views.

ACTIONS DOCUMENTED HERE
-----------------------
SubscriptionManagementViewSet
  1.  checkout            — new  (paid individual subscribe → Stripe Checkout)
  2.  start_trial         — modified (now returns checkout_url, not sub data)
  3.  convert_trial       — existing (no Stripe change, doc tightened)
  4.  upgrade             — modified (now via Stripe Subscription.modify)
  5.  downgrade           — existing (unchanged logic, doc tightened)
  6.  cancel              — modified (now also cancels on Stripe side)
  7.  purchase_overage    — new  (explicit overage block purchase)

LicenseSubscriptionViewSet
  8.  create              — modified (self-serve tiers → Stripe Checkout)
  9.  add_teachers        — existing (unchanged, doc added for completeness)
  10. remove_teachers     — existing (unchanged, doc added for completeness)
  11. process_renewal     — existing (unchanged, doc tightened)
  12. renewal_info        — existing (unchanged, doc tightened)
"""

# ---------------------------------------------------------------------------
# Shared imports needed in views.py / license_views.py
# Add any missing ones to the existing import blocks — do not duplicate.
# ---------------------------------------------------------------------------
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiResponse,
    extend_schema,
    inline_serializer,
)
from rest_framework import serializers

from billing.serializers import LicenseSubscriptionSerializer

# ===========================================================================
# SubscriptionManagementViewSet — Stripe actions
# ===========================================================================

# ---------------------------------------------------------------------------
# 1. checkout
# POST /api/v1/subscription/checkout
# ---------------------------------------------------------------------------
CHECKOUT_SCHEMA = extend_schema(
    tags=["Subscription — Stripe"],
    summary="Create a Stripe Checkout Session for a paid individual subscription",
    description="""
Creates a Stripe-hosted Checkout Session for a new **paid individual
subscription**. The user is redirected to Stripe's payment page; credits are
only granted after Stripe confirms payment via the `checkout.session.completed`
webhook — not when this endpoint is called.

**When to use this vs. `/upgrade/`**

| Situation | Correct endpoint |
|---|---|
| User has **no** active subscription | `/checkout/` |
| User already has an active paid subscription and wants a higher plan | `/upgrade/` |
| User is on a trial and wants to pay early | `/convert-trial/` |

**Flow**
1. Call this endpoint → receive `checkout_url`.
2. Redirect the user to `checkout_url`.
3. After payment, Stripe fires `checkout.session.completed`.
4. Webhook grants credits and creates the `UserSubscription` row.
5. Frontend polls `GET /subscription/me` until `is_active=true`.

**Requirements**
- Plan must be `category=INDIVIDUAL`.
- Plan must have `stripe_price_id` configured.
- User must not already have an active subscription.
""",
    request=inline_serializer(
        name="CheckoutRequest",
        fields={
            "plan": serializers.UUIDField(
                help_text="UUID of the SubscriptionPlan to subscribe to."
            ),
            "success_url": serializers.URLField(
                required=False,
                help_text=(
                    "URL Stripe redirects to after successful payment. "
                    "Defaults to FRONTEND_DOMAIN/billing/success."
                ),
            ),
            "cancel_url": serializers.URLField(
                required=False,
                help_text=(
                    "URL Stripe redirects to if the user abandons checkout. "
                    "Defaults to FRONTEND_DOMAIN/billing/cancelled."
                ),
            ),
        },
    ),
    responses={
        200: OpenApiResponse(
            description="Checkout Session created. Redirect the user to `checkout_url`.",
            response=inline_serializer(
                name="CheckoutResponse",
                fields={
                    "checkout_url": serializers.URLField(
                        help_text="Stripe-hosted payment page URL. Redirect the user here immediately."
                    )
                },
            ),
            examples=[
                OpenApiExample(
                    "Checkout session created",
                    value={
                        "checkout_url": (
                            "https://checkout.stripe.com/pay/cs_test_a1b2c3..."
                        )
                    },
                    response_only=True,
                )
            ],
        ),
        400: OpenApiResponse(
            description=(
                "Validation error. Possible reasons:\n"
                "- `plan` field missing\n"
                "- User already has an active subscription (use `/upgrade/` instead)\n"
                "- Plan has no `stripe_price_id` configured\n"
                "- Plan is not `INDIVIDUAL` category"
            ),
            examples=[
                OpenApiExample(
                    "Already subscribed",
                    value={
                        "detail": (
                            "User already has an active subscription. "
                            "Use change_plan() to switch plans instead of "
                            "creating a new checkout session."
                        )
                    },
                    response_only=True,
                ),
                OpenApiExample(
                    "Missing stripe_price_id",
                    value={
                        "detail": "Plan STANDARD has no stripe_price_id configured."
                    },
                    response_only=True,
                ),
            ],
        ),
        404: OpenApiResponse(description="Plan not found or is not active."),
    },
)


# ---------------------------------------------------------------------------
# 2. upgrade
# POST /api/v1/subscription/upgrade
# ---------------------------------------------------------------------------
UPGRADE_SCHEMA = extend_schema(
    tags=["Subscription — Stripe"],
    summary="Immediately upgrade an existing paid subscription to a higher plan",
    description="""
Upgrades an **existing active paid subscription** to a higher-priced plan
immediately, via `Stripe.Subscription.modify()` with
`proration_behavior=always_invoice`. No Checkout redirect is needed because
the customer's card is already on file.

**What happens**
1. Stripe modifies the subscription item to the new plan's `stripe_price_id`.
2. `always_invoice` makes Stripe immediately create **and attempt to pay**
   an invoice for the prorated difference, synchronously, as part of this
   request.
3. Credits are granted **only if that invoice is actually paid.** If the
   charge is declined or requires further authentication (3D Secure), the
   subscription item is reverted back to the old price and the unpaid
   invoice is voided — Stripe and your account never disagree about which
   plan you're actually paying for.

**3D Secure limitation**
If the proration charge requires 3DS authentication, this endpoint reverts
the change and returns a `400` rather than completing authentication
inline. Update your payment method (e.g. via Checkout for a fresh card) and
retry.

**When to use this vs. other endpoints**

| Situation | Correct endpoint |
|---|---|
| No active subscription yet | `/checkout/` |
| Active paid subscription → **higher**-priced plan | `/upgrade/` |
| Active paid subscription → **lower**-priced plan | `/downgrade/` |
| Currently on a free trial | `/convert-trial/` |

**Validation order** (each returns a specific, actionable message):
no active subscription → on a trial → already on this plan → target plan
is actually cheaper (use downgrade) → subscription has no
`stripe_subscription_id` (manually-granted subscriptions need support).
""",
    request=inline_serializer(
        name="UpgradeRequest",
        fields={
            "plan": serializers.UUIDField(
                help_text="UUID of the higher-priced INDIVIDUAL plan to switch to."
            )
        },
    ),
    responses={
        200: OpenApiResponse(
            response=inline_serializer(
                name="UpgradeResponse",
                fields={
                    "id": serializers.UUIDField(),
                    "plan": serializers.UUIDField(),
                    "is_active": serializers.BooleanField(),
                    "billing_cycle_start": serializers.DateTimeField(),
                    "billing_cycle_end": serializers.DateTimeField(),
                    "stripe_subscription_id": serializers.CharField(),
                    "stripe_status": serializers.CharField(),
                },
            ),
            description="Upgrade payment succeeded. Returns the new subscription with credits already granted.",
            examples=[
                OpenApiExample(
                    "Upgrade successful",
                    value={
                        "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
                        "plan": "9ab85f64-9999-4562-b3fc-2c963f66afa6",
                        "is_active": True,
                        "billing_cycle_start": "2025-06-01T00:00:00Z",
                        "billing_cycle_end": "2025-07-01T00:00:00Z",
                        "stripe_subscription_id": "sub_1OzXXX",
                        "stripe_status": "active",
                    },
                    response_only=True,
                )
            ],
        ),
        400: OpenApiResponse(
            description=(
                "Request rejected, or payment failed and was reverted. "
                "Possible reasons:\n"
                "- `plan` field missing\n"
                "- Plan is not `INDIVIDUAL` category\n"
                "- No active subscription (use `/checkout/`)\n"
                "- Currently on a free trial (use `/convert-trial/`)\n"
                "- Already on the requested plan\n"
                "- Requested plan is cheaper (use `/downgrade/`)\n"
                "- Subscription has no linked Stripe subscription (contact support)\n"
                "- Card declined on the proration charge — plan was reverted\n"
                "- Charge requires 3D Secure authentication — plan was reverted"
            ),
            examples=[
                OpenApiExample(
                    "Wrong direction",
                    value={
                        "detail": (
                            "That plan is cheaper than your current plan. "
                            "Use /subscription/downgrade/ instead."
                        )
                    },
                    response_only=True,
                ),
                OpenApiExample(
                    "Card declined",
                    value={
                        "detail": (
                            "Upgrade payment failed (invoice status: open). "
                            "Your plan has not been changed."
                        )
                    },
                    response_only=True,
                ),
                OpenApiExample(
                    "3DS required",
                    value={
                        "detail": (
                            "Upgrade payment requires additional authentication "
                            "(3D Secure) that can't be completed automatically here. "
                            "Please update your payment method and try again. "
                            "Your plan has not been changed."
                        )
                    },
                    response_only=True,
                ),
            ],
        ),
        404: OpenApiResponse(description="Plan not found or is not active."),
    },
)


# ---------------------------------------------------------------------------
# 3. downgrade
# POST /api/v1/subscription/downgrade
# ---------------------------------------------------------------------------
DOWNGRADE_SCHEMA = extend_schema(
    tags=["Subscription — Stripe"],
    summary="Schedule a subscription downgrade for end of current billing cycle",
    description="""
Schedules a downgrade to a **lower plan** to take effect at the end of the
current billing cycle. The user keeps their current plan and credits until
then.

**What happens**
- `pending_plan`, `pending_change_type` and `pending_change_note` are set
  on the local `UserSubscription`, and a Stripe `SubscriptionSchedule` is
  created so Stripe itself switches price at the cycle boundary.
- `auto_renew` is deliberately left `True`. A downgrade still renews — it
  just renews onto a different plan. Only `cancel` sets `auto_renew` to
  `False`.
- At the next `invoice.payment_succeeded` webhook (end of cycle), the
  renewal logic resolves `pending_plan`, applies rollover, and grants
  credits for the new plan.

**No immediate charge or credit change.** The user is not charged or
refunded anything immediately. The effective change date is the next
`billing_cycle_end`.

**Frontend note.** Read `has_pending_change` (with `pending_plan`,
`pending_plan_effective_date` and `pending_change_message`) to show a
scheduled-change banner. Do NOT infer a scheduled change — or a
cancellation — from `auto_renew`: it is `False` for every free trial as
well. `cancellation.has_pending_cancellation` is the flag for "show the
Resume button".
""",
    request=inline_serializer(
        name="DowngradeRequest",
        fields={
            "plan_id": serializers.UUIDField(
                help_text="UUID of the lower-tier INDIVIDUAL plan to switch to at cycle end."
            )
        },
    ),
    responses={
        200: OpenApiResponse(
            description="Downgrade scheduled. Takes effect at `billing_cycle_end`.",
            response=inline_serializer(
                name="DowngradeResponse",
                fields={
                    "status": serializers.CharField(),
                    "message": serializers.CharField(),
                },
            ),
            examples=[
                OpenApiExample(
                    "Downgrade scheduled",
                    value={
                        "status": "scheduled",
                        "message": (
                            "Downgrade scheduled for the end of the "
                            "current billing cycle"
                        ),
                    },
                    response_only=True,
                )
            ],
        ),
        400: OpenApiResponse(description="No active subscription to downgrade."),
        404: OpenApiResponse(description="Plan not found."),
    },
)


# ---------------------------------------------------------------------------
# 4. cancel
# POST /api/v1/subscription/cancel
# ---------------------------------------------------------------------------
CANCEL_SCHEMA = extend_schema(
    tags=["Subscription — Stripe"],
    summary="Cancel subscription at end of current billing cycle",
    description="""
Cancels the authenticated user's subscription at the **end of the current
billing cycle** (not immediately). The user retains access and credits until
`billing_cycle_end`.

Sets `auto_renew=False` and stamps `cancelled_at` on the local
`UserSubscription`, and sets `cancel_at_period_end=True` on Stripe. Any
previously scheduled plan change is discarded (its Stripe
`SubscriptionSchedule` is released) — the message says so when that happened.

**Frontend note.** After this call the subscription serializer's nested
`cancellation` object reports `has_pending_cancellation: true`, along with
`cancellation_effective_date` (when access ends) and `cancellation_message`
(ready to display, and includes the `cancelled_at` date when known). Gate
the **Resume** button on `cancellation.has_pending_cancellation` — it is
true only while `POST /subscription/resume` would actually succeed.

Do NOT gate it on `auto_renew`: that flag is `False` for every free trial
too, so trial users would be shown a bogus "cancelled" state.
""",
    request=None,
    responses={
        200: OpenApiResponse(
            description=(
                "Cancellation scheduled. Subscription remains active "
                "until `billing_cycle_end`."
            ),
            response=inline_serializer(
                name="CancelResponse",
                fields={
                    "status": serializers.CharField(),
                    "message": serializers.CharField(),
                },
            ),
            examples=[
                OpenApiExample(
                    "Cancellation scheduled",
                    value={
                        "status": "cancelled",
                        "message": (
                            "Subscription will not renew at the end of "
                            "the current billing cycle"
                        ),
                    },
                    response_only=True,
                )
            ],
        ),
        404: OpenApiResponse(
            description="No active subscription found to cancel.",
            examples=[
                OpenApiExample(
                    "No subscription",
                    value={
                        "status": "inactive",
                        "message": "No active subscription found to cancel",
                    },
                    response_only=True,
                )
            ],
        ),
    },
)


# ---------------------------------------------------------------------------
# 5. purchase_overage
# POST /api/v1/subscription/credits/overage/purchase
# ---------------------------------------------------------------------------


# ===========================================================================
# LicenseSubscriptionViewSet — Stripe actions
# ===========================================================================

# ---------------------------------------------------------------------------
# 6. create (license)
# POST /api/v1/license-subscriptions
# ---------------------------------------------------------------------------
LICENSE_CREATE_SCHEMA = extend_schema(
    tags=["License Subscriptions"],
    summary="Create a new license subscription (self-serve tiers → Stripe Checkout)",
    description="""
Creates a new institutional license subscription. For **standard Pro/Power
tiers**, this returns a Stripe Checkout URL — the school admin enters their
card on Stripe's hosted page. The `LicenseSubscription` row and teacher
credit allocations are created **only after** the `checkout.session.completed`
webhook confirms payment.

**Custom/contact-sales plans** (`is_contact_sales=true`) cannot be created
through this endpoint. For those, your team sets up billing manually in the
Stripe Dashboard and attaches the resulting `stripe_subscription_id` via the
superadmin panel.

**Flow for self-serve tiers**
1. Call this endpoint → receive `checkout_url`.
2. Redirect the school admin to `checkout_url`.
3. After payment, Stripe fires `checkout.session.completed`.
4. Webhook calls `LicenseSubscriptionService.create_license_subscription()`,
   enrolls teachers, creates credit wallets and MONTHLY buckets.
5. Frontend polls `GET /license-subscriptions/` to confirm the license exists.

**Teacher enrollment at creation**
Pass `teacher_emails` to enroll teachers immediately. Teachers not yet on
the platform receive an invitation email with an activation link.
Teachers with an existing active individual subscription cannot be enrolled
until that subscription is cancelled.
""",
    request=inline_serializer(
        name="LicenseCreateRequest",
        fields={
            "school": serializers.UUIDField(help_text="UUID of the School."),
            "admin_user": serializers.UUIDField(
                required=False,
                help_text=(
                    "UUID of the school admin managing this license. "
                    "OPTIONAL - omit it and the school's own admin is used, "
                    "which is correct in every ordinary case. Pass it only to "
                    "designate a specific admin when the school has several. "
                    "Must be a SCHOOL_ADMIN/teacher belonging to that school: "
                    "a super admin or a member of another school is rejected."
                ),
            ),
            "plan": serializers.UUIDField(
                help_text="UUID of a LICENSE-category plan. Must not be contact-sales."
            ),
            "contract_months": serializers.IntegerField(
                default=12,
                help_text="Contract duration. Determines how far ahead billing_cycle_end is set.",
            ),
            "max_seats": serializers.IntegerField(
                default=0,
                min_value=0,
                help_text="Maximum teacher seats. 0 = unlimited.",
            ),
            "teacher_emails": serializers.ListField(
                child=serializers.EmailField(),
                required=False,
                help_text=(
                    "Optional list of teacher emails to enroll on creation. "
                    "New accounts receive an activation email. Teachers with "
                    "active individual subscriptions must cancel first."
                ),
            ),
            "custom_price_cents": serializers.IntegerField(
                required=False,
                allow_null=True,
                help_text=(
                    "Optional negotiated monthly price in USD cents, overriding "
                    "the plan's default. If provided, a `price_data` line item "
                    "is used in Checkout instead of the plan's `stripe_price_id`."
                ),
            ),
            "success_url": serializers.URLField(
                required=False,
                help_text=(
                    "Redirect URL after payment. Defaults to "
                    "FRONTEND_DOMAIN/billing/license-success."
                ),
            ),
            "cancel_url": serializers.URLField(
                required=False,
                help_text=(
                    "Redirect URL if admin abandons checkout. "
                    "Defaults to FRONTEND_DOMAIN/billing/license-cancelled."
                ),
            ),
            "billing_method": serializers.ChoiceField(
                choices=["STRIPE", "OFFLINE"],
                required=False,
                help_text="The billing method for this license. Defaults to STRIPE.",
            ),
        },
    ),
    responses={
        201: OpenApiResponse(
            description=("License created immediately (OFFLINE billing method)."),
            response=LicenseSubscriptionSerializer,
        ),
        200: OpenApiResponse(
            description=(
                "Checkout Session created (STRIPE billing method). Redirect the school admin to "
                "`checkout_url`. The license does not exist yet — it is "
                "created by the webhook after payment."
            ),
            response=inline_serializer(
                name="LicenseCreateResponse",
                fields={
                    "checkout_url": serializers.URLField(
                        help_text="Stripe-hosted Checkout URL for the school admin."
                    )
                },
            ),
            examples=[
                OpenApiExample(
                    "License checkout session created",
                    value={
                        "checkout_url": (
                            "https://checkout.stripe.com/pay/cs_test_license_a1b2..."
                        )
                    },
                    response_only=True,
                )
            ],
        ),
        400: OpenApiResponse(
            description=(
                "Validation error. Possible reasons:\n"
                "- Plan is `is_contact_sales=true` (manual setup required)\n"
                "- Plan is not `LICENSE` category\n"
                "- `contract_months` < 1\n"
                "- `teacher_emails` exceeds `max_seats`\n"
                "- Admin user not authorized for this school"
            ),
            examples=[
                OpenApiExample(
                    "Contact-sales plan",
                    value={
                        "error": (
                            "Plan CUSTOM_LICENSE_HIGH is contact-sales only "
                            "and must be set up manually, not through self-serve checkout."
                        )
                    },
                    response_only=True,
                ),
                OpenApiExample(
                    "Wrong category",
                    value={
                        "error": (
                            "License subscriptions require a LICENSE plan, "
                            "not INDIVIDUAL."
                        )
                    },
                    response_only=True,
                ),
            ],
        ),
        403: OpenApiResponse(
            description="Permission denied — school admin or superadmin required."
        ),
    },
)


# ---------------------------------------------------------------------------
# 7. add_teachers (unchanged logic, schema added for completeness)
# POST /api/v1/license-subscriptions/{id}/add-teachers
# ---------------------------------------------------------------------------
ADD_TEACHERS_SCHEMA = extend_schema(
    tags=["License Subscriptions"],
    summary="Enroll teachers in an existing license subscription",
    description="""
Adds one or more teachers to an active license subscription. Each teacher
gets an individual `SchoolCreditAllocation` and a `MONTHLY` credit bucket
equal to `plan.monthly_credits` (subject to any global seat-budget cap).

**New accounts**
Teachers not already on the platform receive an invitation email with a
7-day activation link. The `CreditBucket` and `SchoolCreditAllocation` are
created immediately — teachers can use their credits once they activate.

**Existing accounts**
- Teachers with an active **individual** subscription must cancel it first.
  Their entry appears in `errors` with a conflict message rather than
  failing the entire batch.
- Teachers already enrolled are silently skipped (idempotent).

**Seat cap**
If `max_seats > 0` and adding the batch would exceed it, individual
teachers that push over the cap appear in `errors`; teachers processed
before the cap is hit are still enrolled successfully.
""",
    request=inline_serializer(
        name="AddTeachersRequest",
        fields={
            "teacher_emails": serializers.ListField(
                child=serializers.EmailField(),
                help_text="List of teacher email addresses to enroll.",
            )
        },
    ),
    responses={
        200: OpenApiResponse(
            response=inline_serializer(
                name="AddTeachersResponse",
                fields={
                    "successful": serializers.IntegerField(
                        help_text="Number of teachers successfully enrolled."
                    ),
                    "failed": serializers.IntegerField(
                        help_text="Number of teachers that could not be enrolled."
                    ),
                    "errors": serializers.ListField(
                        child=inline_serializer(
                            name="TeacherEnrollmentError",
                            fields={
                                "teacher_email": serializers.EmailField(),
                                "error": serializers.CharField(),
                            },
                        ),
                        help_text="Per-teacher error detail for each failure.",
                    ),
                },
            ),
            description="Batch processed. Check `failed` and `errors` for partial failures.",
            examples=[
                OpenApiExample(
                    "Partial success",
                    value={
                        "successful": 3,
                        "failed": 1,
                        "errors": [
                            {
                                "teacher_email": "teacher@gmail.com",
                                "error": "Individual subscription conflict or invalid email domain.",
                            }
                        ],
                    },
                    response_only=True,
                ),
                OpenApiExample(
                    "All successful",
                    value={"successful": 4, "failed": 0, "errors": []},
                    response_only=True,
                ),
            ],
        ),
        400: OpenApiResponse(description="`teacher_emails` field missing or empty."),
        403: OpenApiResponse(description="Permission denied."),
        404: OpenApiResponse(description="License subscription not found."),
    },
)


# ---------------------------------------------------------------------------
# 10. remove_teachers
# POST /api/v1/license-subscriptions/{id}/remove-teachers
# ---------------------------------------------------------------------------
REMOVE_TEACHERS_SCHEMA = extend_schema(
    tags=["License Subscriptions"],
    summary="Remove teachers from a license subscription",
    description="""
Removes one or more teachers from an active license. For each teacher:

1. `SchoolCreditAllocation.is_active` is set to `False`.
2. All active credit buckets (MONTHLY, CARRY_OVER, OVERAGE) are expired
   immediately — the teacher loses any unused credits.
3. Historical buckets and ledger entries are preserved for audit purposes.

Teachers can be re-enrolled later via `add-teachers`; they will receive a
fresh credit allocation but lose any credits that expired on removal.

**This action is irreversible for any credits expired at removal time.**
""",
    request=inline_serializer(
        name="RemoveTeachersRequest",
        fields={
            "teacher_ids": serializers.ListField(
                child=serializers.UUIDField(),
                help_text="List of teacher user UUIDs to remove.",
            )
        },
    ),
    responses={
        200: OpenApiResponse(
            response=inline_serializer(
                name="RemoveTeachersResponse",
                fields={
                    "successful": serializers.IntegerField(),
                    "failed": serializers.IntegerField(),
                    "errors": serializers.ListField(
                        child=inline_serializer(
                            name="TeacherRemovalError",
                            fields={
                                "teacher_id": serializers.UUIDField(),
                                "error": serializers.CharField(),
                            },
                        )
                    ),
                },
            ),
            description="Batch processed.",
            examples=[
                OpenApiExample(
                    "All removed",
                    value={"successful": 2, "failed": 0, "errors": []},
                    response_only=True,
                )
            ],
        ),
        400: OpenApiResponse(description="`teacher_ids` field missing or empty."),
        403: OpenApiResponse(description="Permission denied."),
        404: OpenApiResponse(description="License subscription not found."),
    },
)


# ---------------------------------------------------------------------------
# 11. process_renewal (superadmin-only manual trigger)
# POST /api/v1/license-subscriptions/{id}/process-renewal
# ---------------------------------------------------------------------------
PROCESS_RENEWAL_SCHEMA = extend_schema(
    tags=["License Subscriptions"],
    summary="Manually trigger a license renewal (superadmin only)",
    description="""
Manually triggers `LicenseSubscriptionService.process_license_renewal()` for
a specific license. Normally this is called automatically by the
`process_license_renewals` Celery task at `billing_cycle_end`.

**Use cases**
- Testing renewals in staging without waiting for the cycle to end.
- Recovering a license that was skipped due to a Celery failure.

**Idempotency**
`process_license_renewal()` includes an idempotency check: if
`billing_cycle_end` is still in the future, it logs a warning and returns
without making any changes. This prevents double-renewal if called
accidentally on a license that already renewed.

**Stripe note**
This triggers the local credit rollover/allocation logic only. It does not
affect Stripe billing — Stripe continues on its own monthly cadence
regardless.
""",
    request=None,
    responses={
        200: OpenApiResponse(
            description="Renewal processed successfully.",
            response=inline_serializer(
                name="ProcessRenewalResponse",
                fields={"status": serializers.CharField()},
            ),
            examples=[
                OpenApiExample(
                    "Success",
                    value={"status": "Renewal processed successfully"},
                    response_only=True,
                )
            ],
        ),
        400: OpenApiResponse(
            description="Renewal failed (e.g. all teachers failed to renew).",
            examples=[
                OpenApiExample(
                    "All teachers failed",
                    value={"error": "License abc123 renewal failed for all teachers."},
                    response_only=True,
                )
            ],
        ),
        403: OpenApiResponse(description="Superadmin access required."),
        404: OpenApiResponse(description="License not found."),
    },
)


# ---------------------------------------------------------------------------
# 12. renewal_info
# GET /api/v1/license-subscriptions/{id}/renewal-info
# ---------------------------------------------------------------------------
RENEWAL_INFO_SCHEMA = extend_schema(
    tags=["License Subscriptions"],
    summary="Get renewal status and teacher allocation summary for a license",
    description="""
Returns upcoming renewal details and the current state of all teacher
allocations under a license. Useful for building a school admin dashboard
showing when the next renewal fires, how many seats are active, and each
teacher's individual allocation.

**`stripe_status`** reflects the status Stripe last reported for this
license's subscription — `active`, `past_due`, `canceled`, etc. If the
monthly Stripe charge is failing, `stripe_status` will be `past_due` here
before your team intervenes.
""",
    responses={
        200: OpenApiResponse(
            response=inline_serializer(
                name="RenewalInfoResponse",
                fields={
                    "next_renewal_date": serializers.DateTimeField(
                        help_text="Datetime of the next credit rollover / billing cycle end."
                    ),
                    "days_until_renewal": serializers.IntegerField(
                        help_text="Whole days until next renewal. 0 if overdue."
                    ),
                    "auto_renew": serializers.BooleanField(
                        help_text=(
                            "If False, the license will deactivate at "
                            "`next_renewal_date` and Stripe will stop billing."
                        )
                    ),
                    "is_active": serializers.BooleanField(),
                    "stripe_status": serializers.CharField(
                        help_text=(
                            "Last known Stripe subscription status. "
                            "'past_due' means the most recent monthly charge failed."
                        )
                    ),
                    "teacher_count": serializers.IntegerField(
                        help_text="Total active teacher seats."
                    ),
                    "active_teacher_count": serializers.IntegerField(
                        help_text="Teachers with is_active=True allocations."
                    ),
                    "allocations": serializers.ListField(
                        child=serializers.DictField(),
                        help_text=(
                            "Full SchoolCreditAllocation list including "
                            "monthly_allocation per teacher."
                        ),
                    ),
                },
            ),
            description="Renewal and allocation details.",
            examples=[
                OpenApiExample(
                    "License in good standing",
                    value={
                        "next_renewal_date": "2025-07-01T00:00:00Z",
                        "days_until_renewal": 12,
                        "auto_renew": True,
                        "is_active": True,
                        "stripe_status": "active",
                        "teacher_count": 5,
                        "active_teacher_count": 5,
                        "allocations": [
                            {
                                "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
                                "user_email": "teacher1@school.edu",
                                "monthly_allocation": 50000000,
                                "display_monthly_allocation": 50000,
                                "is_active": True,
                            }
                        ],
                    },
                    response_only=True,
                ),
                OpenApiExample(
                    "Payment failing",
                    value={
                        "next_renewal_date": "2025-06-01T00:00:00Z",
                        "days_until_renewal": 0,
                        "auto_renew": True,
                        "is_active": True,
                        "stripe_status": "past_due",
                        "teacher_count": 3,
                        "active_teacher_count": 3,
                        "allocations": [],
                    },
                    response_only=True,
                ),
            ],
        ),
        403: OpenApiResponse(description="Permission denied."),
        404: OpenApiResponse(description="License not found."),
    },
)
