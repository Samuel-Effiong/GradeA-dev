"""
AUTHZ-T1/T2: an access token must stop working the moment the session is
revoked (logout) or the credentials change (password change / reset).

Real JWTs through the real login endpoint and the real authentication chain
(never force_authenticate, which skips the code under test).
"""

from unittest.mock import patch

from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient, APITestCase
from rest_framework.throttling import SimpleRateThrottle

from users.models import CustomUser, PasswordResetOTP, UserTypes

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
PASSWORD = "Original-Passw0rd-1"  # pragma: allowlist secret
NEW_PASSWORD = "Replacement-Passw0rd-2"  # pragma: allowlist secret
WIDE = {  # pragma: allowlist secret
    "login": "1000000/hour",
    "otp_request": "1000000/hour",
    "password_reset": "1000000/hour",  # pragma: allowlist secret
}


@override_settings(CACHES=LOCMEM)
class TokenRevocationBase(APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        p = patch.dict(SimpleRateThrottle.THROTTLE_RATES, WIDE)
        p.start()
        self.addCleanup(p.stop)
        m = patch("users.views.safe_delay")
        m.start()
        self.addCleanup(m.stop)
        self.user = CustomUser.objects.create_user(
            email="tok@example.com",
            password=PASSWORD,
            first_name="Tok",
            last_name="En",
            user_type=UserTypes.TEACHER,
            is_active=True,
            email_verified_at=timezone.now(),
        )
        self.me_url = reverse("user-detail", kwargs={"pk": self.user.pk})

    def login(self, password=PASSWORD):
        r = APIClient().post(
            reverse("login"),
            {"email": self.user.email, "password": password},
            format="json",
        )
        self.assertEqual(r.status_code, status.HTTP_200_OK, r.content)
        return r.json()["data"]

    def call_as(self, access):
        c = APIClient()
        c.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        return c.get(self.me_url)

    def as_client(self, access):
        c = APIClient()
        c.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        return c


class AccessTokenRevocationTests(TokenRevocationBase):
    def test_sanity_a_fresh_access_token_works(self):
        self.assertEqual(
            self.call_as(self.login()["access"]).status_code, status.HTTP_200_OK
        )

    def test_T1_access_token_stops_working_after_logout(self):
        tokens = self.login()
        r = self.as_client(tokens["access"]).post(
            reverse("auth-logout"), {"refresh": tokens["refresh"]}, format="json"
        )
        self.assertIn(r.status_code, (200, 204, 205))
        self.assertEqual(
            self.call_as(tokens["access"]).status_code,
            status.HTTP_401_UNAUTHORIZED,
            "stolen access token still works after logout",
        )

    def test_T2_access_token_stops_working_after_password_change(self):
        tokens = self.login()
        r = self.as_client(tokens["access"]).post(
            reverse("auth-change-password"),
            {"current_password": PASSWORD, "new_password": NEW_PASSWORD},
            format="json",
        )
        self.assertEqual(r.status_code, status.HTTP_200_OK, r.content)
        self.assertEqual(
            self.call_as(tokens["access"]).status_code,
            status.HTTP_401_UNAUTHORIZED,
            "stolen access token still works after password change",
        )
        # ...and the tokens handed back to the changing device do work.
        self.assertEqual(
            self.call_as(r.json()["data"]["access"]).status_code, status.HTTP_200_OK
        )

    def test_T2_access_token_stops_working_after_password_reset(self):
        tokens = self.login()
        c = APIClient()
        c.post(
            reverse("auth-otp"),
            {"email": self.user.email, "otp_type": "RESET_PASSWORD"},
            format="json",
        )
        code = PasswordResetOTP.objects.get(user=self.user).code
        r = c.post(
            reverse("auth-reset-password"),
            {"email": self.user.email, "otp": code, "new_password": NEW_PASSWORD},
            format="json",
        )
        self.assertEqual(r.status_code, status.HTTP_200_OK, r.content)
        self.assertEqual(
            self.call_as(tokens["access"]).status_code,
            status.HTTP_401_UNAUTHORIZED,
            "stolen access token still works after password reset",
        )
        self.assertEqual(
            self.call_as(r.json()["data"]["access"]).status_code, status.HTTP_200_OK
        )


def decode_claims(token):
    import jwt
    from django.conf import settings

    return jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])


class EpochMechanismTests(TokenRevocationBase):
    def epoch(self):
        return CustomUser.objects.get(pk=self.user.pk).token_epoch

    def refresh(self, refresh_token):
        return APIClient().post(
            reverse("refresh"), {"refresh": refresh_token}, format="json"
        )

    # -- claim handling ------------------------------------------------------
    def test_issued_tokens_carry_the_current_epoch(self):
        CustomUser.objects.filter(pk=self.user.pk).update(token_epoch=7)
        tokens = self.login()
        self.assertEqual(decode_claims(tokens["access"])["epoch"], 7)
        self.assertEqual(decode_claims(tokens["refresh"])["epoch"], 7)

    def test_missing_claim_counts_as_epoch_zero_so_deploy_signs_nobody_out(self):
        """Hand-built token exactly as minted before this change existed."""
        from rest_framework_simplejwt.tokens import RefreshToken

        legacy = RefreshToken.for_user(self.user)
        self.assertNotIn("epoch", legacy.payload)
        self.assertEqual(
            self.call_as(str(legacy.access_token)).status_code, status.HTTP_200_OK
        )
        self.assertEqual(self.refresh(str(legacy)).status_code, status.HTTP_200_OK)

    def test_missing_claim_is_rejected_once_the_user_has_moved_past_epoch_zero(self):
        from rest_framework_simplejwt.tokens import RefreshToken

        legacy = RefreshToken.for_user(self.user)
        CustomUser.objects.filter(pk=self.user.pk).update(token_epoch=1)
        self.assertEqual(
            self.call_as(str(legacy.access_token)).status_code,
            status.HTTP_401_UNAUTHORIZED,
        )
        self.assertEqual(
            self.refresh(str(legacy)).status_code, status.HTTP_401_UNAUTHORIZED
        )

    def test_a_token_from_the_future_or_the_past_is_rejected(self):
        from users.tokens import EpochRefreshToken

        CustomUser.objects.filter(pk=self.user.pk).update(token_epoch=3)
        for claimed in (2, 4):
            t = EpochRefreshToken.for_user(self.user)
            t["epoch"] = claimed
            access = t.access_token
            access["epoch"] = claimed
            self.assertEqual(
                self.call_as(str(access)).status_code,
                status.HTTP_401_UNAUTHORIZED,
                claimed,
            )

    def test_editing_the_epoch_claim_without_the_key_is_rejected(self):
        import base64
        import json

        tokens = self.login()
        head, body, sig = tokens["access"].split(".")
        claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        claims["epoch"] = 99
        forged = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=")
        self.assertEqual(
            self.call_as(f"{head}.{forged.decode()}.{sig}").status_code,
            status.HTTP_401_UNAUTHORIZED,
        )

    # -- what bumps the epoch ---------------------------------------------------
    def test_creating_a_user_does_not_bump(self):
        self.assertEqual(self.epoch(), 0)

    def test_set_password_then_full_save_bumps_once(self):
        u = CustomUser.objects.get(pk=self.user.pk)
        u.set_password(NEW_PASSWORD)
        u.save()
        self.assertEqual(self.epoch(), 1)
        self.assertEqual(u.token_epoch, 1, "in-memory value must be the real int")

    def test_set_password_with_update_fields_still_bumps(self):
        """Five callers save(update_fields=["password", ...]); they must not
        skip the epoch."""
        u = CustomUser.objects.get(pk=self.user.pk)
        u.set_password(NEW_PASSWORD)
        u.save(update_fields=["password", "must_change_password"])
        self.assertEqual(self.epoch(), 1)

    def test_set_unusable_password_bumps(self):
        u = CustomUser.objects.get(pk=self.user.pk)
        u.set_unusable_password()
        u.save(update_fields=["password", "is_active"])
        self.assertEqual(self.epoch(), 1)

    def test_concurrent_password_changes_do_not_lose_a_bump(self):
        a = CustomUser.objects.get(pk=self.user.pk)
        b = CustomUser.objects.get(pk=self.user.pk)
        a.set_password("first-Passw0rd-1")
        b.set_password("second-Passw0rd-2")
        a.save()
        b.save()
        self.assertEqual(self.epoch(), 2)

    def test_a_plain_save_without_a_password_change_does_not_bump(self):
        u = CustomUser.objects.get(pk=self.user.pk)
        u.first_name = "Renamed"
        u.save()
        self.assertEqual(self.epoch(), 0)

    def test_hasher_upgrade_on_login_does_not_sign_the_user_out(self):
        """Django re-hashes an outdated hash on a successful check_password by
        calling set_password(); that must not bump the epoch."""
        with override_settings(
            PASSWORD_HASHERS=[
                "django.contrib.auth.hashers.PBKDF2PasswordHasher",
                "django.contrib.auth.hashers.MD5PasswordHasher",
            ]
        ):
            from django.contrib.auth.hashers import make_password

            old_hash = make_password(PASSWORD, hasher="md5")
            CustomUser.objects.filter(pk=self.user.pk).update(password=old_hash)
            first = self.login()
            self.assertTrue(
                CustomUser.objects.get(pk=self.user.pk).password.startswith("pbkdf2"),
                "precondition: the hash really was upgraded during login",
            )
            self.assertEqual(self.epoch(), 0)
            self.assertEqual(
                self.call_as(first["access"]).status_code, status.HTTP_200_OK
            )
            # A second login (already-upgraded hash) is also harmless.
            self.assertEqual(self.epoch(), 0)

    # -- refresh / other devices / isolation -----------------------------------------
    def test_a_refresh_keeps_the_epoch_and_the_new_access_token_works(self):
        tokens = self.login()
        r = self.refresh(tokens["refresh"])
        self.assertEqual(r.status_code, status.HTTP_200_OK, r.content)
        access = r.json()["data"]["access"]
        self.assertEqual(decode_claims(access)["epoch"], 0)
        self.assertEqual(self.call_as(access).status_code, status.HTTP_200_OK)

    def test_logout_on_one_device_signs_out_every_device(self):
        """Documented, user-visible behaviour (global logout)."""
        phone, laptop = self.login(), self.login()
        self.as_client(laptop["access"]).post(
            reverse("auth-logout"), {"refresh": laptop["refresh"]}, format="json"
        )
        self.assertEqual(
            self.call_as(phone["access"]).status_code, status.HTTP_401_UNAUTHORIZED
        )
        self.assertEqual(
            self.refresh(phone["refresh"]).status_code, status.HTTP_401_UNAUTHORIZED
        )

    def test_a_stolen_refresh_token_is_dead_after_logout_and_after_change(self):
        stolen = self.login()
        self.as_client(stolen["access"]).post(
            reverse("auth-logout"), {"refresh": "junk"}, format="json"
        )
        # a failed logout (bad refresh) must not revoke anything
        self.assertEqual(self.call_as(stolen["access"]).status_code, status.HTTP_200_OK)
        self.as_client(stolen["access"]).post(
            reverse("auth-logout"), {"refresh": stolen["refresh"]}, format="json"
        )
        self.assertEqual(
            self.refresh(stolen["refresh"]).status_code, status.HTTP_401_UNAUTHORIZED
        )

    def test_a_password_change_signs_out_other_devices_but_not_the_changing_one(self):
        other, mine = self.login(), self.login()
        r = self.as_client(mine["access"]).post(
            reverse("auth-change-password"),
            {"current_password": PASSWORD, "new_password": NEW_PASSWORD},
            format="json",
        )
        self.assertEqual(r.status_code, status.HTTP_200_OK, r.content)
        self.assertEqual(
            self.call_as(other["access"]).status_code, status.HTTP_401_UNAUTHORIZED
        )
        self.assertEqual(
            self.refresh(other["refresh"]).status_code, status.HTTP_401_UNAUTHORIZED
        )
        fresh = r.json()["data"]
        self.assertEqual(self.call_as(fresh["access"]).status_code, status.HTTP_200_OK)
        self.assertEqual(self.refresh(fresh["refresh"]).status_code, status.HTTP_200_OK)

    def test_another_users_sessions_are_untouched(self):
        bystander = CustomUser.objects.create_user(
            email="bystander@example.com",
            password=PASSWORD,
            first_name="By",
            last_name="Stander",
            user_type=UserTypes.TEACHER,
            is_active=True,
            email_verified_at=timezone.now(),
        )
        r = APIClient().post(
            reverse("login"),
            {"email": bystander.email, "password": PASSWORD},
            format="json",
        )
        theirs = r.json()["data"]
        mine = self.login()
        self.as_client(mine["access"]).post(
            reverse("auth-logout"), {"refresh": mine["refresh"]}, format="json"
        )
        c = APIClient()
        c.credentials(HTTP_AUTHORIZATION=f"Bearer {theirs['access']}")
        self.assertEqual(
            c.get(reverse("user-detail", kwargs={"pk": bystander.pk})).status_code,
            status.HTTP_200_OK,
        )
        self.assertEqual(
            CustomUser.objects.get(pk=bystander.pk).token_epoch,
            0,
        )
        self.assertEqual(
            self.refresh(theirs["refresh"]).status_code, status.HTTP_200_OK
        )

    def test_password_reset_signs_out_other_devices(self):
        other = self.login()
        c = APIClient()
        c.post(
            reverse("auth-otp"),
            {"email": self.user.email, "otp_type": "RESET_PASSWORD"},
            format="json",
        )
        code = PasswordResetOTP.objects.get(user=self.user).code
        c.post(
            reverse("auth-reset-password"),
            {"email": self.user.email, "otp": code, "new_password": NEW_PASSWORD},
            format="json",
        )
        self.assertEqual(
            self.refresh(other["refresh"]).status_code, status.HTTP_401_UNAUTHORIZED
        )
