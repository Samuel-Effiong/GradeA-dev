from io import BytesIO

from django.core.exceptions import ValidationError
from PIL import Image

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
