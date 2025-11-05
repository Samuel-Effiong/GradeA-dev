import uuid

from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import UniqueConstraint
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from users.services import OTPManager

otp_manager = OTPManager()


# Create your models here.
class CustomUserManager(BaseUserManager):
    def create_user(self, email, password=None, save=True, **extra_fields):
        """Create and save a user with the given email and password"""
        if not email:
            raise ValueError(_("The Email must be set"))

        # if 'date_of_birth' in extra_fields:
        #     dob = extra_fields['date_of_birth']
        #     Validators.validate_prevent_future_date(dob)

        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        if save:
            user.save(using=self._db)

        return user

    def create_superuser(self, email, password=None, **extra_fields):
        """Create and save a superuser with the given email and password"""
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError(_("Superuser must have is_staff=True"))
        if extra_fields.get("is_superuser") is not True:
            raise ValueError(_("Superuser must have is_superuser=True"))

        return self.create_user(email, password, **extra_fields)


class UserTypes(models.TextChoices):
    STUDENT = "STUDENT", "Student"
    TEACHER = "TEACHER", "Teacher"


# Create your models here.
class CustomUser(AbstractUser):
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["first_name", "last_name"]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    email = models.EmailField(unique=True)
    user_type = models.CharField(
        max_length=20,
        choices=UserTypes.choices,
        default=UserTypes.TEACHER,
    )
    username = None
    is_active = models.BooleanField(
        _("active"),
        default=False,
        help_text=_(
            "Designates whether this user should be treated as active. "
            "Unselect this instead of deleting accounts."
        ),
    )

    objects = CustomUserManager()

    activation_token = models.CharField(
        _("Activation Token"), max_length=64, null=True, blank=True, db_index=True
    )
    activation_expires = models.DateTimeField(
        _("Activation Expires"),
        null=True,
        blank=True,
    )
    email_verified_at = models.DateTimeField(blank=True, null=True)

    def __str__(self):
        return f"{self.first_name} {self.last_name}"

    class Meta:
        ordering = ("first_name", "last_name", "email")
        verbose_name = "User"
        verbose_name_plural = "Users"

    def is_student(self):
        if self.user_type == UserTypes.STUDENT:
            return True
        return False

    def is_teacher(self):
        if self.user_type == UserTypes.TEACHER:
            return True
        return False

    def renew_activation_token(self):
        """Renew the activation token and extend expiration."""
        self.activation_token = otp_manager.generate_otp()

        if self.user_type == UserTypes.STUDENT:
            self.activation_expires = timezone.now() + timezone.timedelta(days=7)
        else:
            raise ValueError(
                "Activation token renewal is only for students. Use OTP (VERIFY_EMAIL) endpoint for teachers."
            )
        self.save()
        return self.activation_token


class PasswordResetOTP(models.Model):
    user = models.OneToOneField(CustomUser, on_delete=models.CASCADE)
    code = models.CharField(
        max_length=100, unique=True, null=True, blank=True, db_index=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            UniqueConstraint(
                fields=["user", "code"], name="password_reset_unique_user_code_per_user"
            )
        ]

    def is_valid(self):
        return (timezone.now() - self.created_at) < timezone.timedelta(minutes=15)

    def generate_code(self):
        self.code = otp_manager.generate_otp()
        self.save()

        return self.code

    def __str__(self):
        return f"OTP for {self.user.email}"


class PasswordChangeOTP(models.Model):
    user = models.OneToOneField(CustomUser, on_delete=models.CASCADE)
    code = models.CharField(
        max_length=100, unique=True, null=True, blank=True, db_index=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            UniqueConstraint(
                fields=["user", "code"],
                name="password_change_unique_user_code_per_user",
            )
        ]

    def is_valid(self):
        # A simple check for a 5-minute validity period
        return (timezone.now() - self.created_at) < timezone.timedelta(minutes=5)

    def generate_code(self):
        # Generate a 6-digit numeric OTP
        self.code = otp_manager.generate_otp()
        self.save()

        return self.code
