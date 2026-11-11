from rest_framework import serializers

from .models import AuditEvent

_TARGET_LABEL = {
    "CustomUser": "User account",
    "StudentSubmission": "Student submission",
    "Assignment": "Assignment",
    "AuditEvent": "Audit event",
    "Course": "Course",
    "School": "School",
    "Session": "Session",
    "LicenseStripeMutationIntent": "License change intent",
    "CreditLedger": "Credit ledger",
    # Epic B1a: label approved by the owner (SM 2026-11-06).
    "AIJob": "Credit hold job",
    # Epic B1c: from the owner-approved "Credit request made/answered".
    "CreditRequest": "Credit request",
    # Epic B1b-4: the billing_settings command (the model's verbose_name).
    "BillingSettings": "Billing settings",
    "SchoolCreditAllocation": "Credit allocation",
    "StudentCourse": "Student enrollment",
    "UserSubscription": "User subscription",
    "LicenseSubscription": "License subscription",
    "SchoolViewSet": "School",
    "CustomUserViewSet": "User account",
    "CourseViewSet": "Course",
    "AssignmentViewSet": "Assignment",
    "AdminCreditManagementViewSet": "Credit management",
    "BillingTimeTravelView": "Billing time travel",
    "BillingTransactionViewSet": "Billing transaction",
    "CreditBucketViewSet": "Credit bucket",
    "CreditLedgerViewSet": "Credit ledger",
    "CreditUsageLogViewSet": "Credit usage log",
    "CreditWalletViewSet": "Credit wallet",
    "LicenseOverageOfflineRequestViewSet": "Overage request",
    "LicenseSubscriptionViewSet": "License subscription",
    "PaymentMethodViewSet": "Payment method",
    "SchoolCreditAllocationViewSet": "Credit allocation",
    "SettingsViewSet": "Settings",
    "SubscriptionManagementViewSet": "Subscription management",
    "SubscriptionPlanViewSet": "Subscription plan",
    "WaitlistViewSet": "Waitlist",
    "BetaProfileViewSet": "Beta profile",
    "BetaWhitelistViewSet": "Beta whitelist",
    "BetaAnalyticViewSet": "Beta analytics",
    "BetaAnaylicChartViewSet": "Beta analytics chart",
    "SuperAdminDashboardView": "Dashboard",
    "SessionViewSet": "Session",
    "TopicViewSet": "Topic",
    "StudentCourseViewSet": "Student enrollment",
    "StudentSubmissionViewSet": "Student submission",
    "TaskViewSet": "Task",
    "CourseCategoryViewSet": "Course category",
    "StudentAdminDashboardView": "Student dashboard",
    "TeacherAdminDashboardView": "Teacher dashboard",
}


class AuditEventSerializer(serializers.ModelSerializer):
    """Read-only projection of an AuditEvent row for the query API (§8)."""

    target_label = serializers.SerializerMethodField()
    target_name = serializers.SerializerMethodField()

    def get_target_label(self, obj):
        return _TARGET_LABEL.get(obj.target_type)

    def get_target_name(self, obj):
        if obj.target_type != "School" or obj.target_id is None:
            return None
        school_names = self.context.get("school_names") or {}
        return school_names.get(obj.target_id)

    class Meta:
        model = AuditEvent
        fields = [
            "id",
            "occurred_at",
            "actor_id",
            "actor_role",
            "actor_email",
            "school_id",
            "department_id",
            "action",
            "target_type",
            "target_id",
            "target_label",
            "target_name",
            "outcome",
            "error_class",
            "reason_code",
            "trace_id",
            "client_correlation_id",
            "source_ip",
            "user_agent",
            "retention_class",
            "before",
            "after",
            "metadata",
        ]
        read_only_fields = fields
