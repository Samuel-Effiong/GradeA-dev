from rest_framework.routers import DefaultRouter

from .views import (
    BetaAnalyticViewSet,
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

router = DefaultRouter(trailing_slash=False)
router.register(
    r"subscription-plans", SubscriptionPlanViewSet, basename="subscription-plan"
)
router.register(
    r"user-subscriptions", UserSubscriptionViewSet, basename="user-subscription"
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

router.register(
    r"admin/credits", AdminCreditManagementViewSet, basename="admin-credits"
)

urlpatterns = router.urls
