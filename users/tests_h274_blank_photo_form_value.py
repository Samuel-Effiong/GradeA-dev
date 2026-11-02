"""H-274 (e.2a): an empty form value for the profile photo keeps the photo.

Owner decision (2026-11-02, via SM): on PATCH /users/<id>, a multipart form
that sends ``profile_image=""`` must leave the stored photo untouched (200,
photo kept). JSON ``{"profile_image": null}`` stays the way to remove the
photo. Before the change DRF mapped the empty form value to ``None`` (the
model field is ``null=True``) and so removed the photo.

Frontend risk, written into NOTES: if the frontend's remove button sends
multipart ``profile_image=""``, it stops removing the photo until the
frontend sends JSON ``null`` instead.
"""

from io import BytesIO

from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image
from rest_framework import status
from rest_framework.test import APIClient

from users.tests_h270_profile_webp import ProfileWebPTestCase
from users.tests_patch_password import make
from users.tokens import EpochRefreshToken


def _png(colour="red"):
    buf = BytesIO()
    Image.new("RGB", (8, 8), colour).save(buf, format="PNG")
    return SimpleUploadedFile("face.png", buf.getvalue(), content_type="image/png")


class BlankPhotoFormValueTests(ProfileWebPTestCase):
    def setUp(self):
        super().setUp()
        self.teacher = make("blank.photo@gmail.com")
        self.client = APIClient()
        self.client.credentials(
            HTTP_AUTHORIZATION=(
                f"Bearer {EpochRefreshToken.for_user(self.teacher).access_token}"
            )
        )
        self.url = reverse("user-detail", kwargs={"pk": self.teacher.pk})
        response = self.client.patch(
            self.url, {"profile_image": _png()}, format="multipart"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.teacher.refresh_from_db()
        self.photo = self.teacher.profile_image.name
        self.assertTrue(self.photo)
        self.assertTrue(default_storage.exists(self.photo))

    def assertPhotoKept(self):
        self.teacher.refresh_from_db()
        self.assertEqual(self.teacher.profile_image.name, self.photo)
        self.assertTrue(default_storage.exists(self.photo))

    # The owner decision.

    def test_an_empty_form_value_keeps_the_photo(self):
        response = self.client.patch(
            self.url, {"profile_image": ""}, format="multipart"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertPhotoKept()

    def test_an_empty_form_value_beside_other_fields_keeps_the_photo(self):
        response = self.client.patch(
            self.url, {"profile_image": "", "bio": "New bio"}, format="multipart"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertPhotoKept()
        self.assertEqual(self.teacher.bio, "New bio")

    # What must not change with it.

    def test_json_null_still_removes_the_photo(self):
        response = self.client.patch(self.url, {"profile_image": None}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.teacher.refresh_from_db()
        self.assertFalse(self.teacher.profile_image)

    def test_a_new_photo_in_a_form_still_replaces_the_old_one(self):
        response = self.client.patch(
            self.url, {"profile_image": _png("blue")}, format="multipart"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.teacher.refresh_from_db()
        self.assertTrue(self.teacher.profile_image)
        self.assertNotEqual(self.teacher.profile_image.name, self.photo)

    def test_a_json_empty_string_is_still_refused(self):
        response = self.client.patch(self.url, {"profile_image": ""}, format="json")
        self.assertEqual(
            response.status_code, status.HTTP_400_BAD_REQUEST, response.content
        )
        self.assertPhotoKept()
