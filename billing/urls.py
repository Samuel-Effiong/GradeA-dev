from django.urls import path
from rest_framework.routers import DefaultRouter

from .billing_transaction_views import BillingTransactionViewSet
from .license_overage_offline_views import LicenseOverageOfflineRequestViewSet
from .license_views import LicenseSubscriptionViewSet, SchoolCreditAllocationViewSet
from .payment_method_views import PaymentMethodViewSet
from .qa_console import (
    qa_console_action,
    qa_console_new_subscriber,
    qa_console_page,
    qa_console_reset,
    qa_console_runs_create,
    qa_console_runs_detail,
    qa_console_runs_list,
    qa_console_state,
)
from .qa_time_travel import BillingTimeTravelView
from .views import (
    BetaAnalyticViewSet,
    BetaAnaylicChartViewSet,
    BetaProfileViewSet,
    CreditBucketViewSet,
    CreditLedgerViewSet,
    CreditUsageLogViewSet,
    CreditWalletViewSet,
    SubscriptionManagementViewSet,
    SubscriptionPlanViewSet,
    UserSubscriptionViewSet,
)
from .views_admin_credits import AdminCreditManagementViewSet
from .webhooks import stripe_webhook, thin_webhook

router = DefaultRouter(trailing_slash=False)
router.register(
    r"subscription-plans", SubscriptionPlanViewSet, basename="subscription-plan"
)
router.register(
    r"user-subscriptions", UserSubscriptionViewSet, basename="user-subscription"
)
router.register(
    r"license-subscriptions",
    LicenseSubscriptionViewSet,
    basename="license-subscription",
)
router.register(
    r"school-credit-allocations",
    SchoolCreditAllocationViewSet,
    basename="school-credit-allocation",
)
router.register(r"credit-wallets", CreditWalletViewSet, basename="credit-wallet")
router.register(r"credit-buckets", CreditBucketViewSet, basename="credit-bucket")
router.register(r"credit-ledgers", CreditLedgerViewSet, basename="credit-ledger")
router.register(
    r"credit-usage-logs", CreditUsageLogViewSet, basename="credit-usage-log"
)
router.register(r"subscription", SubscriptionManagementViewSet, basename="subscription")
router.register(r"analytics", BetaAnalyticViewSet, basename="analytics")
router.register(r"beta-profile", BetaProfileViewSet, basename="beta-profile")
router.register(r"beta-chart", BetaAnaylicChartViewSet, basename="beta-chart")
router.register(r"invoices", BillingTransactionViewSet, basename="billing-invoice")
router.register(r"payment-methods", PaymentMethodViewSet, basename="payment-method")

router.register(
    r"admin/credits", AdminCreditManagementViewSet, basename="admin-credits"
)
router.register(
    r"license-overage-offline-requests",
    LicenseOverageOfflineRequestViewSet,
    basename="license-overage-offline-request",
)


urlpatterns = router.urls + [
    path("stripe/webhooks", stripe_webhook, name="stripe-webhook"),
    path("stripe/webhooks/thin", thin_webhook, name="stripe-webhook-thin"),
    path("qa/time-travel", BillingTimeTravelView.as_view(), name="qa-time-travel"),
    path("qa/console", qa_console_page, name="qa-console"),
    path("qa/console/state", qa_console_state, name="qa-console-state"),
    path(
        "qa/console/subscriber",
        qa_console_new_subscriber,
        name="qa-console-new-subscriber",
    ),
    path("qa/console/action", qa_console_action, name="qa-console-action"),
    path("qa/console/reset", qa_console_reset, name="qa-console-reset"),
    path("qa/console/runs", qa_console_runs_list, name="qa-console-runs-list"),
    path(
        "qa/console/runs/create",
        qa_console_runs_create,
        name="qa-console-runs-create",
    ),
    path(
        "qa/console/runs/<uuid:run_id>",
        qa_console_runs_detail,
        name="qa-console-runs-detail",
    ),
]
