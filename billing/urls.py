from rest_framework.routers import DefaultRouter

from .views import (
    CreditBucketViewSet,
    CreditLedgerViewSet,
    CreditUsageLogViewSet,
    CreditWalletViewSet,
    SubscriptionManagementViewSet,
    SubscriptionPlanViewSet,
    UserSubscriptionViewSet,
)

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

urlpatterns = router.urls
