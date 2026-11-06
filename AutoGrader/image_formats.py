import os
from io import BytesIO

from django import forms
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from PIL import Image, ImageCms, ImageOps
from rest_framework import serializers

from audit.enums import ReasonCode
from AutoGrader.reason_codes import REASON_CODES
from AutoGrader.uploads import file_name_of

PILLOW_FORMATS = {
    "image/jpeg": "JPEG",
    "image/png": "PNG",
    "image/gif": "GIF",
    "image/webp": "WEBP",
}

_ALLOWED_PILLOW_NAMES = tuple(sorted(PILLOW_FORMATS.values()))


def validate_image_format(value):
    """Reject uploaded images whose Pillow format is not in the allowed set."""
    pos = value.tell()
    try:
        img = Image.open(BytesIO(value.read()), formats=_ALLOWED_PILLOW_NAMES)
        img.verify()
    except Exception as exc:
        raise ValidationError(
            "Upload a JPEG, PNG, GIF or WEBP image.",
            code="invalid_image_format",
        ) from exc
    finally:
        value.seek(pos)


def _empty_upload_message(upload):
    return REASON_CODES[ReasonCode.FILE_EMPTY].render(
        {"file_name": file_name_of(upload)}
    )


# H-274 (e.2b): a 0-byte profile picture is refused with (e.1)'s FILE_EMPTY
# text at every door that stores one, in place of Django's "The submitted
# file is empty." The error code stays "empty". These checks run before DRF's
# and Django's own empty-file checks, which stay in place behind them.


class ProfileImageField(serializers.ImageField):
    def to_internal_value(self, data):
        if getattr(data, "size", None) == 0:
            raise serializers.ValidationError(_empty_upload_message(data), code="empty")
        return super().to_internal_value(data)


class ProfileImageFormField(forms.ImageField):
    def to_python(self, data):
        if getattr(data, "size", None) == 0:
            raise ValidationError(_empty_upload_message(data), code="empty")
        return super().to_python(data)


# H-270: profile pictures are stored as WebP. Phone photos carry EXIF with
# GPS, so the stored file keeps the pixels and nothing else.
PROFILE_IMAGE_MAX_SIDE = 1024
PROFILE_IMAGE_WEBP_QUALITY = 80

_SRGB = ImageCms.createProfile("sRGB")


def _has_transparency(img):
    return img.mode in ("RGBA", "LA", "PA") or (
        img.mode == "P" and "transparency" in img.info
    )


def to_profile_webp(upload):
    """Re-encode an uploaded profile picture as a metadata-free WebP.

    The first frame only, turned upright from its EXIF orientation, colour
    converted to sRGB when it carries an ICC profile, longest side at most
    PROFILE_IMAGE_MAX_SIDE. The pixels are copied into a fresh image, so no
    EXIF, XMP or ICC data can reach the encoder. Returns a ContentFile named
    "<original stem>.webp".
    """
    upload.seek(0)
    opened = Image.open(BytesIO(upload.read()), formats=_ALLOWED_PILLOW_NAMES)
    opened.seek(0)
    img: Image.Image = ImageOps.exif_transpose(opened)

    mode = "RGBA" if _has_transparency(img) else "RGB"
    icc = img.info.get("icc_profile")
    if icc:
        try:
            converted = ImageCms.profileToProfile(
                img, ImageCms.ImageCmsProfile(BytesIO(icc)), _SRGB, outputMode=mode
            )
            if converted is not None:
                img = converted
        except (ImageCms.PyCMSError, OSError, ValueError):
            # A profile this image's mode can't use, or a broken one: keep
            # the pixels as they are. The profile is dropped either way.
            pass
    img = img.convert(mode)
    img.thumbnail(
        (PROFILE_IMAGE_MAX_SIDE, PROFILE_IMAGE_MAX_SIDE), Image.Resampling.LANCZOS
    )

    clean = Image.new(mode, img.size)
    clean.paste(img)
    out = BytesIO()
    clean.save(out, format="WEBP", quality=PROFILE_IMAGE_WEBP_QUALITY, method=6)

    stem = os.path.splitext(os.path.basename(upload.name or "profile"))[0]
    return ContentFile(out.getvalue(), name=f"{stem}.webp")
