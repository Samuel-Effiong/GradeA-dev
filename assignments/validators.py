from io import BytesIO

from django.core.exceptions import ValidationError
from PIL import Image

ALLOWED_IMAGE_FORMATS = {"JPEG", "PNG", "GIF", "WEBP"}


def validate_image_format(value):
    """Reject uploaded images whose Pillow format is not in the allowed set."""
    pos = value.tell()
    try:
        img = Image.open(BytesIO(value.read()), formats=list(ALLOWED_IMAGE_FORMATS))
        img.verify()
    except Exception as exc:
        raise ValidationError(
            "Upload a JPEG, PNG, GIF or WEBP image.",
            code="invalid_image_format",
        ) from exc
    finally:
        value.seek(pos)
