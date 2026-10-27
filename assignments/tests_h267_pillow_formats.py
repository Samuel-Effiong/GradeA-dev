"""H-267: Image.open must restrict Pillow to the four allowed formats.

Red-run expectations (written before the code):
  1. test_psd_labelled_as_png_is_refused — today the PSD decoder runs and
     may succeed or raise; after the fix, formats= blocks it at open time
     and the except clause maps it to FileUnreadableError (422).
  2. test_bmp_labelled_as_png_is_refused — same: BMP is a valid image but
     not in our allowed set; formats= must block it.
  3. test_tiff_labelled_as_png_is_refused — same for TIFF.
  4. test_allowed_formats_match_image_formats — the PILLOW_FORMATS mapping
     must exactly cover IMAGE_FORMATS; today neither mapping nor formats=
     exists.
  5. test_profile_image_rejects_bmp — the profile_image serializer field
     today accepts any format Django/Pillow can read; after the fix, a
     shared validator rejects formats outside the four allowed.
  6. test_profile_image_accepts_jpeg — the validator must not reject
     allowed formats.
"""

from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from PIL import Image

from assignments.exceptions import FileUnreadableError
from assignments.services import AssignmentProcessingService
from AutoGrader.image_formats import PILLOW_FORMATS


def _make_image(fmt, content_type, filename="test_img"):
    """Build a real image in the given Pillow format, wrapped as an upload."""
    img = Image.new("RGB", (4, 4), "red")
    buf = BytesIO()
    img.save(buf, format=fmt)
    buf.seek(0)
    ext = {
        "JPEG": "jpg",
        "PNG": "png",
        "GIF": "gif",
        "WEBP": "webp",
        "BMP": "bmp",
        "TIFF": "tiff",
    }.get(fmt, fmt.lower())
    return SimpleUploadedFile(
        f"{filename}.{ext}",
        buf.getvalue(),
        content_type=content_type,
    )


def _make_psd_bytes():
    """Minimal PSD file header (version 1, 1×1 RGB)."""
    import struct

    sig = b"8BPS"
    version = struct.pack(">H", 1)
    reserved = b"\x00" * 6
    channels = struct.pack(">H", 3)
    height = struct.pack(">I", 1)
    width = struct.pack(">I", 1)
    depth = struct.pack(">H", 8)
    color_mode = struct.pack(">H", 3)  # RGB
    # Empty colour mode data, image resources, layer info
    color_data = struct.pack(">I", 0)
    img_resources = struct.pack(">I", 0)
    layer_info = struct.pack(">I", 0)
    # Image data: compression=0 (raw), 3 channels × 1 byte each
    img_data = struct.pack(">H", 0) + b"\xff" * 3
    return (
        sig
        + version
        + reserved
        + channels
        + height
        + width
        + depth
        + color_mode
        + color_data
        + img_resources
        + layer_info
        + img_data
    )


def _make_corrupt_png():
    """Valid PNG with one flipped byte in IDAT data; CRC left unchanged.

    Image.open(formats=("PNG",)) accepts the header, but verify() detects
    the CRC mismatch on the IDAT chunk and raises.
    """
    img = Image.new("RGB", (4, 4), "red")
    buf = BytesIO()
    img.save(buf, format="PNG")
    raw = bytearray(buf.getvalue())
    idat_pos = raw.find(b"IDAT")
    assert idat_pos != -1, "No IDAT chunk in generated PNG"
    raw[idat_pos + 4] ^= 0xFF
    return bytes(raw)


class UploadFormatRestrictionTests(TestCase):
    """formats= on _compress_uploaded_image blocks decoders outside the set."""

    def test_psd_labelled_as_png_is_refused(self):
        psd = SimpleUploadedFile(
            "fake.png", _make_psd_bytes(), content_type="image/png"
        )
        with self.assertRaises(FileUnreadableError) as ctx:
            AssignmentProcessingService._compress_uploaded_image(psd)
        self.assertEqual(ctx.exception.status_code, 422)

    def test_bmp_labelled_as_png_is_refused(self):
        bmp = _make_image("BMP", "image/png", "fake")
        with self.assertRaises(FileUnreadableError) as ctx:
            AssignmentProcessingService._compress_uploaded_image(bmp)
        self.assertEqual(ctx.exception.status_code, 422)

    def test_tiff_labelled_as_png_is_refused(self):
        tiff = _make_image("TIFF", "image/png", "fake")
        with self.assertRaises(FileUnreadableError) as ctx:
            AssignmentProcessingService._compress_uploaded_image(tiff)
        self.assertEqual(ctx.exception.status_code, 422)

    def test_jpeg_labelled_correctly_is_accepted(self):
        jpg = _make_image("JPEG", "image/jpeg")
        result = AssignmentProcessingService._compress_uploaded_image(jpg)
        self.assertIsInstance(result, bytes)
        self.assertGreater(len(result), 0)

    def test_png_labelled_correctly_is_accepted(self):
        png = _make_image("PNG", "image/png")
        result = AssignmentProcessingService._compress_uploaded_image(png)
        self.assertIsInstance(result, bytes)

    def test_gif_labelled_correctly_is_accepted(self):
        gif = _make_image("GIF", "image/gif")
        result = AssignmentProcessingService._compress_uploaded_image(gif)
        self.assertIsInstance(result, bytes)

    def test_webp_labelled_correctly_is_accepted(self):
        webp = _make_image("WEBP", "image/webp")
        result = AssignmentProcessingService._compress_uploaded_image(webp)
        self.assertIsInstance(result, bytes)


class FormatMappingTests(TestCase):
    """The PILLOW_FORMATS mapping must match IMAGE_FORMATS exactly."""

    def test_allowed_formats_match_image_formats(self):
        self.assertEqual(
            set(PILLOW_FORMATS.keys()),
            set(AssignmentProcessingService.IMAGE_FORMATS),
        )

    def test_pillow_formats_values_are_uppercase_strings(self):
        for mime, pillow_name in PILLOW_FORMATS.items():
            self.assertEqual(
                pillow_name,
                pillow_name.upper(),
                f"{mime} maps to {pillow_name!r}, expected uppercase",
            )

    def test_validator_accepts_exactly_the_four_allowed_formats(self):
        from AutoGrader.image_formats import validate_image_format

        for fmt in ("JPEG", "PNG", "GIF", "WEBP"):
            img = _make_image(fmt, f"image/{fmt.lower()}")
            validate_image_format(img)  # should not raise

    def test_validator_rejects_bmp(self):
        from django.core.exceptions import ValidationError

        from AutoGrader.image_formats import validate_image_format

        bmp = _make_image("BMP", "image/bmp")
        with self.assertRaises(ValidationError):
            validate_image_format(bmp)

    def test_validator_rejects_corrupt_png(self):
        from django.core.exceptions import ValidationError

        from AutoGrader.image_formats import validate_image_format

        corrupt = _make_corrupt_png()
        upload = SimpleUploadedFile("corrupt.png", corrupt, content_type="image/png")
        with self.assertRaises(ValidationError):
            validate_image_format(upload)


class ProfileImageFormatTests(TestCase):
    """Profile image fields reject formats outside the allowed four."""

    def test_direct_add_student_rejects_bmp(self):
        from classrooms.serializers import DirectAddStudentSerializer

        bmp = _make_image("BMP", "image/bmp", "headshot")
        data = {
            "first_name": "Test",
            "last_name": "Student",
            "profile_image": bmp,
        }
        ser = DirectAddStudentSerializer(data=data)
        self.assertFalse(ser.is_valid())
        self.assertIn("profile_image", ser.errors)

    def test_direct_add_student_accepts_jpeg(self):
        from classrooms.serializers import DirectAddStudentSerializer

        jpg = _make_image("JPEG", "image/jpeg", "headshot")
        data = {
            "first_name": "Test",
            "last_name": "Student",
            "profile_image": jpg,
        }
        ser = DirectAddStudentSerializer(data=data)
        if not ser.is_valid():
            self.assertNotIn("profile_image", ser.errors)

    def test_student_registration_completion_rejects_bmp(self):
        from classrooms.serializers import StudentRegistrationCompletionSerializer

        bmp = _make_image("BMP", "image/bmp", "headshot")
        data = {
            "first_name": "Test",
            "last_name": "Student",
            "password": "TestP@ss1234",  # pragma: allowlist secret
            "token": "faketoken",
            "profile_image": bmp,
        }
        ser = StudentRegistrationCompletionSerializer(data=data)
        self.assertFalse(ser.is_valid())
        self.assertIn("profile_image", ser.errors)

    def test_student_registration_completion_accepts_png(self):
        from classrooms.serializers import StudentRegistrationCompletionSerializer

        png = _make_image("PNG", "image/png", "headshot")
        data = {
            "first_name": "Test",
            "last_name": "Student",
            "password": "TestP@ss1234",  # pragma: allowlist secret
            "token": "faketoken",
            "profile_image": png,
        }
        ser = StudentRegistrationCompletionSerializer(data=data)
        if not ser.is_valid():
            self.assertNotIn("profile_image", ser.errors)

    def test_school_with_admin_rejects_bmp(self):
        from classrooms.serializers import SchoolWithAdminSerializer

        bmp = _make_image("BMP", "image/bmp", "headshot")
        data = {
            "school_name": "Test School",
            "admin_email": "admin@testschool.edu",
            "admin_first_name": "Test",
            "admin_last_name": "Admin",
            "admin_profile_image": bmp,
        }
        ser = SchoolWithAdminSerializer(data=data)
        self.assertFalse(ser.is_valid())
        self.assertIn("admin_profile_image", ser.errors)

    def test_custom_user_serializer_rejects_bmp(self):
        from users.serializers import CustomUserSerializer

        bmp = _make_image("BMP", "image/bmp", "headshot")
        data = {
            "email": "test@example.com",
            "first_name": "Test",
            "last_name": "User",
            "password": "StrongP@ss1234",  # pragma: allowlist secret
            "profile_image": bmp,
        }
        ser = CustomUserSerializer(data=data)
        self.assertFalse(ser.is_valid())
        self.assertIn("profile_image", ser.errors)

    def test_admin_form_rejects_bmp(self):
        from django.utils.datastructures import MultiValueDict

        from users.admin import _ProfileImageForm

        bmp = _make_image("BMP", "image/bmp", "headshot")
        form = _ProfileImageForm(
            data={"email": "t@example.com", "first_name": "T", "last_name": "U"},
            files=MultiValueDict({"profile_image": [bmp]}),
        )
        form.is_valid()
        self.assertIn("profile_image", form.errors)
