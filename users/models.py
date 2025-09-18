from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils.translation import gettext_lazy as _


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

    email = models.EmailField(unique=True)
    user_type = models.CharField(
        max_length=20,
        choices=UserTypes.choices,
        default=UserTypes.TEACHER,
    )
    username = None
    objects = CustomUserManager()

    def __str__(self):
        return f"{self.first_name} {self.last_name}"

    class Meta:
        ordering = ("first_name", "last_name", "email")
        verbose_name = "User"
        verbose_name_plural = "Users"
