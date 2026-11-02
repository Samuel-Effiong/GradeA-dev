"""H-274 (e.2), BUG-028: an empty profile photo must not be stored.

Reproduction at d297ddbf, before any code: every door that stores a profile
picture already refuses a 0-byte file, through DRF's / Django's own
``allow_empty_file=False`` (code ``empty``), so the server needs no change.
These tests keep that true. Each write path is driven through its own entry
point, as in users/tests_h270_profile_webp.py: the user endpoint over HTTP
with a real JWT, DirectAddStudentSerializer, SchoolWithAdminSerializer,
StudentRegistrationCompletionSerializer and the admin user form.
"""

from io import BytesIO

from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils.datastructures import MultiValueDict
from PIL import Image
from rest_framework import status
from rest_framework.test import APIClient

from users.tests_h270_profile_webp import ProfileWebPTestCase
from users.tests_patch_password import make
from users.tokens import EpochRefreshToken


def _empty(name="face.png", content_type="image/png"):
    return SimpleUploadedFile(name, b"", content_type=content_type)


def _png():
    buf = BytesIO()
    Image.new("RGB", (8, 8), "red").save(buf, format="PNG")
    return SimpleUploadedFile("face.png", buf.getvalue(), content_type="image/png")


class EmptyPhotoOverHttpTests(ProfileWebPTestCase):
    def setUp(self):
        super().setUp()
        self.teacher = make("empty.photo@gmail.com")
        self.client = APIClient()
        self.client.credentials(
            HTTP_AUTHORIZATION=(
                f"Bearer {EpochRefreshToken.for_user(self.teacher).access_token}"
            )
        )
        self.url = reverse("user-detail", kwargs={"pk": self.teacher.pk})

    def patch(self, upload):
        return self.client.patch(
            self.url, {"profile_image": upload}, format="multipart"
        )

    def assertRefused(self, response):
        self.assertEqual(
            response.status_code, status.HTTP_400_BAD_REQUEST, response.content
        )
        body = response.json()
        self.assertIs(body["success"], False)
        self.assertTrue(body["error"]["field_errors"]["profile_image"])

    def test_a_zero_byte_photo_is_refused_and_nothing_is_stored(self):
        self.assertRefused(self.patch(_empty()))
        self.teacher.refresh_from_db()
        self.assertFalse(self.teacher.profile_image)

    def test_a_zero_byte_file_without_an_image_type_is_refused(self):
        self.assertRefused(self.patch(_empty("face", "application/octet-stream")))
        self.teacher.refresh_from_db()
        self.assertFalse(self.teacher.profile_image)

    def test_a_zero_byte_photo_leaves_the_current_photo_in_place(self):
        response = self.patch(_png())
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.teacher.refresh_from_db()
        before = self.teacher.profile_image.name
        self.assertTrue(default_storage.exists(before))

        self.assertRefused(self.patch(_empty()))
        self.teacher.refresh_from_db()
        self.assertEqual(self.teacher.profile_image.name, before)
        self.assertTrue(default_storage.exists(before))

    def test_bytes_that_are_not_an_image_are_refused(self):
        bad = SimpleUploadedFile("face.png", b"not an image", content_type="image/png")
        self.assertRefused(self.patch(bad))
        self.teacher.refresh_from_db()
        self.assertFalse(self.teacher.profile_image)


class EmptyPhotoAtEveryOtherDoorTests(ProfileWebPTestCase):
    def assertEmptyCode(self, errors):
        self.assertEqual([e.code for e in errors], ["empty"], errors)

    def test_direct_add_student_refuses_it(self):
        from classrooms.models import Course, Session
        from classrooms.serializers import DirectAddStudentSerializer

        teacher = make("door.teach@gmail.com")
        session = Session.objects.create(name="H274 term", teacher=teacher)
        course = Course.objects.create(name="H274", teacher=teacher, session=session)
        ser = DirectAddStudentSerializer(
            data={"first_name": "Stu", "last_name": "Dent", "profile_image": _empty()},
            context={"course": course},
        )
        self.assertFalse(ser.is_valid())
        self.assertEmptyCode(ser.errors["profile_image"])

    def test_school_with_admin_refuses_it(self):
        from classrooms.serializers import SchoolWithAdminSerializer

        ser = SchoolWithAdminSerializer(
            data={
                "school_name": "H274 School",
                "admin_email": "admin@h274school.test",
                "admin_first_name": "Ad",
                "admin_last_name": "Min",
                "admin_profile_image": _empty(),
            }
        )
        self.assertFalse(ser.is_valid())
        self.assertEmptyCode(ser.errors["admin_profile_image"])

    def test_student_registration_completion_refuses_it(self):
        from classrooms.serializers import StudentRegistrationCompletionSerializer

        ser = StudentRegistrationCompletionSerializer(data={"profile_image": _empty()})
        self.assertFalse(ser.is_valid())
        self.assertEmptyCode(ser.errors["profile_image"])

    def test_the_admin_user_form_refuses_it(self):
        from users.admin import _ProfileImageForm

        user = make("admin.form@gmail.com")
        form = _ProfileImageForm(
            instance=user,
            data={},
            files=MultiValueDict({"profile_image": [_empty()]}),
        )
        self.assertFalse(form.is_valid())
        self.assertEqual(
            [e.code for e in form.errors.as_data()["profile_image"]], ["empty"]
        )
