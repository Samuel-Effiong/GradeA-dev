import base64
import logging
from io import BytesIO
from typing import List

import requests
from bs4 import BeautifulSoup
from PIL import Image

logger = logging.getLogger(__name__)

IMAGE_COMPRESSION_TARGET_BYTES = int(1.5 * 1024 * 1024)
IMAGE_COMPRESSION_HARD_CAP_BYTES = 4 * 1024 * 1024
IMAGE_COMPRESSION_QUALITY_STEPS = [85, 75, 65, 55, 45]
IMAGE_COMPRESSION_SCALE_STEPS = [1.0, 0.85, 0.7, 0.55]
IMAGE_COMPRESSION_MIN_DIMENSION = 1000


class ImageCompressionError(Exception):
    """Raised when an image cannot be compressed under the hard size cap."""


def perform_search(urls: List[str]):
    results = {}

    for url in urls:
        try:
            res = requests.get(url)
            res.raise_for_status()
            soup = BeautifulSoup(res.text, "lxml")

            for script in soup(["script", "style"]):
                script.decompose()

            text = soup.get_text(separator="\n", strip=True)
            results[url] = text
        except Exception as e:
            results[url] = f"Error fetching {url}: {e}"
    return results


def encode_image(uploaded_file=None, image_byte=None):
    if uploaded_file is not None:
        byte = uploaded_file.read()
    elif image_byte is not None:
        byte = image_byte
    return base64.b64encode(byte).decode()


def safe_sort_key(x):
    return int(x) if str(x).isdigit() else str(x)


def _to_rgb(image: Image.Image) -> Image.Image:
    if image.mode in ("RGBA", "LA") or (
        image.mode == "P" and "transparency" in image.info
    ):
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.split()[-1])
        return background
    return image.convert("RGB")


def compress_image_for_upload(
    image: Image.Image,
    target_bytes: int = IMAGE_COMPRESSION_TARGET_BYTES,
    hard_cap_bytes: int = IMAGE_COMPRESSION_HARD_CAP_BYTES,
) -> bytes:
    """Re-encode an image as JPEG, iteratively reducing quality and then
    dimensions until it fits under target_bytes. Raises ImageCompressionError
    if it cannot get under hard_cap_bytes even at the quality/dimension floor.
    """
    rgb_image = _to_rgb(image)
    original_size = rgb_image.size

    attempts = 0
    best_bytes = None

    for scale in IMAGE_COMPRESSION_SCALE_STEPS:
        if scale == 1.0:
            candidate = rgb_image
        else:
            width, height = original_size
            new_width = max(int(width * scale), IMAGE_COMPRESSION_MIN_DIMENSION)
            new_height = max(int(height * scale), IMAGE_COMPRESSION_MIN_DIMENSION)
            if max(new_width, new_height) >= max(width, height):
                continue
            candidate = rgb_image.resize((new_width, new_height), Image.LANCZOS)

        for quality in IMAGE_COMPRESSION_QUALITY_STEPS:
            attempts += 1
            buffered = BytesIO()
            candidate.save(buffered, format="JPEG", quality=quality, optimize=True)
            data = buffered.getvalue()

            if best_bytes is None or len(data) < len(best_bytes):
                best_bytes = data

            if len(data) <= target_bytes:
                if attempts > 1:
                    logger.warning(
                        "Image compression required %d attempt(s) "
                        "(scale=%.2f, quality=%d) to reach %d bytes",
                        attempts,
                        scale,
                        quality,
                        len(data),
                    )
                return data

    if best_bytes is not None and len(best_bytes) <= hard_cap_bytes:
        logger.warning(
            "Image compression could not reach target of %d bytes; "
            "falling back to smallest achieved size of %d bytes",
            target_bytes,
            len(best_bytes),
        )
        return best_bytes

    raise ImageCompressionError(
        "Unable to compress image under the maximum allowed size "
        f"({hard_cap_bytes} bytes) even at minimum quality/dimensions."
    )
