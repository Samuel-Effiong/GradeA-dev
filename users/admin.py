from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.utils.translation import gettext_lazy as _

from .models import CustomUser, PasswordChangeOTP, PasswordResetOTP


@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    list_display = (
        "email",
        "first_name",
        "last_name",
        "user_type",
        "is_active",
        "email_verified_at",
        "is_staff",
        "school",
    )
    list_filter = (
        "user_type",
        "is_active",
        "is_staff",
        "is_superuser",
        "date_joined",
        "email_verified_at",
        "school",
    )
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        (_("Personal info"), {"fields": ("first_name", "last_name")}),
        (_("School info"), {"fields": ("school", "user_type")}),
        (
            _("Account verification"),
            {
                "fields": (
                    "is_active",
                    "email_verified_at",
                    "activation_token",
                    "activation_expires",
                )
            },
        ),
        (
            _("Permissions"),
            {
                "fields": ("is_staff", "is_superuser", "groups", "user_permissions"),
            },
        ),
        (_("Important dates"), {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "email",
                    "first_name",
                    "last_name",
                    "user_type",
                    "school",
                    "password1",
                    "password2",
                ),
            },
        ),
    )
    search_fields = ("email", "first_name", "last_name")
    ordering = ("first_name",)
    date_hierarchy = "date_joined"
    filter_horizontal = ("groups", "user_permissions")

    def get_readonly_fields(self, request, obj=None):
        # Make the UUID id field read-only when editing existing users
        if obj:
            return ["id", "date_joined", "last_login"]
        return []

    actions = ["activate_users", "deactivate_users"]

    @admin.action(description="Mark selected users as active")
    def activate_users(self, request, queryset):
        updated = queryset.update(is_active=True)
        self.message_user(request, f"{updated} users were successfully activated.")

    @admin.action(description="Mark selected users as inactive")
    def deactivate_users(self, request, queryset):
        updated = queryset.update(is_active=False)
        self.message_user(request, f"{updated} users were successfully deactivated.")


@admin.register(PasswordResetOTP)
class PasswordResetOTPAdmin(admin.ModelAdmin):
    list_display = ("user", "code", "created_at", "is_valid_otp")
    list_filter = ("created_at",)
    search_fields = ("user__email", "code")
    readonly_fields = ("created_at",)
    raw_id_fields = ("user",)
    date_hierarchy = "created_at"

    @admin.display(boolean=True, description="Is Valid")
    def is_valid_otp(self, obj):
        return obj.is_valid()

    is_valid_otp.boolean = True
    is_valid_otp.short_description = "Is Valid"

    def has_add_permission(self, request):
        # Prevent manual creation - OTPs should be generated through the model's methods
        return False


@admin.register(PasswordChangeOTP)
class PasswordChangeOTPAdmin(admin.ModelAdmin):
    list_display = ("user", "code", "created_at", "is_valid_otp")
    list_filter = ("created_at",)
    search_fields = ("user__email", "code")
    readonly_fields = ("created_at",)
    raw_id_fields = ("user",)
    date_hierarchy = "created_at"

    @admin.display(boolean=True, description="Is Valid")
    def is_valid_otp(self, obj):
        return obj.is_valid()

    is_valid_otp.boolean = True
    is_valid_otp.short_description = "Is Valid"

    def has_add_permission(self, request):
        # Prevent manual creation - OTPs should be generated through the model's methods
        return False
