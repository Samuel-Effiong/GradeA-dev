"""H-270: profile pictures are stored as WebP with no metadata.

Design: docs/evidence/h270-profile-webp/DESIGN.md. The conversion runs in
CustomUser.save(), so every write path is checked through its own entry
point: the model, CustomUserSerializer, DirectAddStudentSerializer,
SchoolWithAdminSerializer and the admin user form.
(StudentRegistrationCompletionSerializer validates a picture but no view
uses it, so nothing it receives is stored.)

Red as written against the call-site revert (the save() hook removed):
every test that expects a WebP fails; the two guards (a save that keeps
the picture, an old JPEG picture) pass either way.
"""

import shutil
import struct
import tempfile
from io import BytesIO

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import ExifTags, Image, ImageCms

from users.models import CustomUser, UserTypes

PASSWORD = "H270-Pr0file-Pic!"  # pragma: allowlist secret
_MEDIA_ROOT = tempfile.mkdtemp(prefix="h270_media_")


def _upload(img, fmt, name, **save_kwargs):
    buf = BytesIO()
    img.save(buf, format=fmt, **save_kwargs)
    return SimpleUploadedFile(name, buf.getvalue(), content_type=f"image/{fmt.lower()}")


def _gps_exif(orientation=None):
    exif = Image.Exif()
    if orientation is not None:
        exif[ExifTags.Base.Orientation] = orientation
    exif[ExifTags.IFD.GPSInfo] = {
        ExifTags.GPS.GPSLatitudeRef: "N",
        ExifTags.GPS.GPSLatitude: (51.0, 30.0, 0.0),
        ExifTags.GPS.GPSLongitudeRef: "W",
        ExifTags.GPS.GPSLongitude: (0.0, 7.0, 0.0),
    }
    return exif.tobytes()


def _riff_chunks(data):
    """The four-character chunk ids of a WebP (RIFF) file."""
    assert data[:4] == b"RIFF" and data[8:12] == b"WEBP", data[:16]
    ids, pos = [], 12
    while pos + 8 <= len(data):
        fourcc, size = (
            data[pos : pos + 4],
            struct.unpack("<I", data[pos + 4 : pos + 8])[0],
        )
        ids.append(fourcc)
        pos += 8 + size + (size & 1)
    return ids


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    },
    MEDIA_ROOT=_MEDIA_ROOT,
)
class ProfileWebPTestCase(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_MEDIA_ROOT, ignore_errors=True)

    def make_user(self, email, **extra):
        return CustomUser.objects.create_user(
            email=email,
            password=PASSWORD,
            first_name="Pic",
            last_name="Ture",
            **extra,
        )

    def stored(self, user):
        user.refresh_from_db()
        with default_storage.open(user.profile_image.name, "rb") as f:
            return f.read()

    def assertStoredAsWebP(self, user):
        data = self.stored(user)
        self.assertTrue(
            user.profile_image.name.endswith(".webp"), user.profile_image.name
        )
        self.assertEqual(Image.open(BytesIO(data)).format, "WEBP")
        return data

    def assertNoMetadata(self, data):
        chunks = _riff_chunks(data)
        for fourcc in (b"EXIF", b"XMP ", b"ICCP"):
            self.assertNotIn(fourcc, chunks)
        self.assertNotIn("exif", Image.open(BytesIO(data)).info)


class EveryWritePathStoresWebPTests(ProfileWebPTestCase):
    def jpeg(self):
        return _upload(Image.new("RGB", (40, 30), "red"), "JPEG", "face.jpg")

    def test_the_model_stores_a_new_picture_as_webp(self):
        user = self.make_user("model@h270.test", profile_image=self.jpeg())
        self.assertStoredAsWebP(user)

    def test_a_replaced_picture_is_stored_as_webp(self):
        user = self.make_user("replace@h270.test")
        user.profile_image = self.jpeg()
        user.save()
        self.assertStoredAsWebP(user)

    def test_the_user_serializer_stores_webp(self):
        from users.serializers import CustomUserSerializer

        user = self.make_user("serializer@h270.test")
        ser = CustomUserSerializer(
            instance=user, data={"profile_image": self.jpeg()}, partial=True
        )
        self.assertTrue(ser.is_valid(), ser.errors)
        ser.save()
        self.assertStoredAsWebP(user)

    def test_direct_add_student_stores_webp(self):
        from classrooms.models import Course, Session
        from classrooms.serializers import DirectAddStudentSerializer

        teacher = self.make_user("teacher@h270.test", user_type=UserTypes.TEACHER)
        session = Session.objects.create(name="H270 term", teacher=teacher)
        course = Course.objects.create(name="H270", teacher=teacher, session=session)
        ser = DirectAddStudentSerializer(
            data={
                "first_name": "Stu",
                "last_name": "Dent",
                "profile_image": self.jpeg(),
            },
            context={"course": course},
        )
        self.assertTrue(ser.is_valid(), ser.errors)
        self.assertStoredAsWebP(ser.save())

    def test_school_with_admin_stores_webp(self):
        from classrooms.serializers import SchoolWithAdminSerializer

        ser = SchoolWithAdminSerializer(
            data={
                "school_name": "H270 School",
                "admin_email": "admin@h270school.test",
                "admin_first_name": "Ad",
                "admin_last_name": "Min",
                "admin_profile_image": self.jpeg(),
            }
        )
        self.assertTrue(ser.is_valid(), ser.errors)
        ser.save()
        self.assertStoredAsWebP(CustomUser.objects.get(email="admin@h270school.test"))

    def test_the_admin_user_form_stores_webp(self):
        from django.http import QueryDict
        from django.utils.datastructures import MultiValueDict

        from users.admin import _ProfileImageForm

        user = self.make_user("adminform@h270.test")
        unbound = _ProfileImageForm(instance=user)
        data = QueryDict(mutable=True)
        for name, field in unbound.fields.items():
            if field.disabled or name == "profile_image":
                continue
            value = unbound[name].value()
            if value is None or value is False:
                continue
            if isinstance(value, (list, tuple)):
                data.setlist(name, [str(v) for v in value])
            else:
                data[name] = str(value)
        form = _ProfileImageForm(
            instance=user,
            data=data,
            files=MultiValueDict({"profile_image": [self.jpeg()]}),
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assertStoredAsWebP(user)


class WhatTheStoredPictureKeepsTests(ProfileWebPTestCase):
    def store(self, upload, email="keeps@h270.test"):
        user = self.make_user(email, profile_image=upload)
        return user, self.assertStoredAsWebP(user)

    def test_gps_and_orientation_are_gone_and_the_picture_is_upright(self):
        # 40 wide x 20 high, tagged "rotate 90 CW to display": upright it is
        # 20 wide x 40 high.
        upload = _upload(
            Image.new("RGB", (40, 20), "blue"),
            "JPEG",
            "phone.jpg",
            exif=_gps_exif(orientation=6),
        )
        _, data = self.store(upload)
        self.assertNoMetadata(data)
        self.assertEqual(Image.open(BytesIO(data)).size, (20, 40))

    def test_a_webp_upload_loses_its_exif_too(self):
        upload = _upload(
            Image.new("RGB", (16, 16), "green"),
            "WEBP",
            "already.webp",
            exif=_gps_exif(),
        )
        self.assertIn(b"EXIF", _riff_chunks(upload.read()))
        upload.seek(0)
        _, data = self.store(upload)
        self.assertNoMetadata(data)

    def test_an_icc_profile_is_not_stored(self):
        icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        upload = _upload(
            Image.new("RGB", (16, 16), "white"), "PNG", "tagged.png", icc_profile=icc
        )
        _, data = self.store(upload)
        self.assertNoMetadata(data)
        self.assertNotIn("icc_profile", Image.open(BytesIO(data)).info)

    def test_transparency_is_kept(self):
        img = Image.new("RGBA", (10, 10), (255, 0, 0, 255))
        img.putpixel((0, 0), (0, 0, 0, 0))
        _, data = self.store(_upload(img, "PNG", "logo.png"))
        out = Image.open(BytesIO(data))
        self.assertEqual(out.mode, "RGBA")
        alpha = out.getchannel("A")
        self.assertEqual(alpha.getpixel((0, 0)), 0)
        self.assertEqual(alpha.getpixel((5, 5)), 255)

    def test_an_animated_gif_is_stored_as_its_first_frame(self):
        frames = [Image.new("RGB", (12, 12), c) for c in ("red", "blue", "green")]
        buf = BytesIO()
        frames[0].save(buf, format="GIF", save_all=True, append_images=frames[1:])
        upload = SimpleUploadedFile(
            "wave.gif", buf.getvalue(), content_type="image/gif"
        )
        _, data = self.store(upload)
        self.assertNotIn(b"ANIM", _riff_chunks(data))
        out = Image.open(BytesIO(data))
        self.assertEqual(getattr(out, "n_frames", 1), 1)
        red, _, blue = out.convert("RGB").split()
        self.assertGreater(min(red.tobytes()), 200)
        self.assertLess(max(blue.tobytes()), 60)

    def test_a_large_picture_is_shrunk_to_1024_on_its_longest_side(self):
        upload = _upload(Image.new("RGB", (3000, 1500), "gray"), "JPEG", "big.jpg")
        _, data = self.store(upload)
        self.assertEqual(Image.open(BytesIO(data)).size, (1024, 512))

    def test_a_small_picture_is_not_enlarged(self):
        upload = _upload(Image.new("RGB", (200, 100), "gray"), "PNG", "small.png")
        _, data = self.store(upload)
        self.assertEqual(Image.open(BytesIO(data)).size, (200, 100))


class SavesThatKeepThePictureTests(ProfileWebPTestCase):
    """Guards: green before and after the change."""

    def test_a_save_that_keeps_the_picture_does_not_re_encode_it(self):
        upload = _upload(Image.new("RGB", (20, 20), "red"), "PNG", "keep.png")
        user = self.make_user("keep@h270.test", profile_image=upload)
        user.refresh_from_db()
        name, data = user.profile_image.name, self.stored(user)

        user.first_name = "Renamed"
        user.save()
        user.save()

        self.assertEqual(self.stored(user), data)
        self.assertEqual(user.profile_image.name, name)

    def test_an_old_jpeg_picture_still_loads_unchanged(self):
        from users.serializers import CustomUserSerializer

        buf = BytesIO()
        Image.new("RGB", (20, 20), "red").save(buf, format="JPEG")
        path = default_storage.save(
            "profile_pics/old/legacy.jpg", ContentFile(buf.getvalue())
        )
        user = self.make_user("legacy@h270.test")
        CustomUser.objects.filter(pk=user.pk).update(profile_image=path)

        user.refresh_from_db()
        user.save()
        user.refresh_from_db()

        self.assertEqual(user.profile_image.name, path)
        self.assertEqual(self.stored(user), buf.getvalue())
        self.assertTrue(
            CustomUserSerializer(user).data["profile_image"].endswith("legacy.jpg")
        )
