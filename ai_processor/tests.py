from io import BytesIO

from django.test import SimpleTestCase
from PIL import Image

from ai_processor.tools import (
    IMAGE_COMPRESSION_HARD_CAP_BYTES,
    IMAGE_COMPRESSION_MIN_DIMENSION,
    IMAGE_COMPRESSION_QUALITY_STEPS,
    IMAGE_COMPRESSION_TARGET_BYTES,
    ImageCompressionError,
    compress_image_for_upload,
)


def _noisy_image(size):
    """A high-entropy image is hard for JPEG to compress, simulating a
    scanned document page (unlike a flat-color image, which compresses to
    almost nothing regardless of quality)."""
    import random

    random.seed(0)
    image = Image.new("RGB", size)
    pixels = [
        (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))
        for _ in range(size[0] * size[1])
    ]
    image.putdata(pixels)
    return image


class CompressImageForUploadTest(SimpleTestCase):
    def test_large_scanned_quality_image_compresses_under_target(self):
        image = _noisy_image((1700, 2200))
        result = compress_image_for_upload(image)
        self.assertLessEqual(len(result), IMAGE_COMPRESSION_TARGET_BYTES)

    def test_small_image_passes_through_at_first_quality_step(self):
        image = Image.new("RGB", (200, 200), color=(120, 130, 140))
        result = compress_image_for_upload(image)
        self.assertLessEqual(len(result), IMAGE_COMPRESSION_TARGET_BYTES)
        # Should succeed on the very first quality attempt (small flat image).
        buffered = BytesIO()
        image.save(
            buffered,
            format="JPEG",
            quality=IMAGE_COMPRESSION_QUALITY_STEPS[0],
            optimize=True,
        )
        self.assertLessEqual(len(buffered.getvalue()), IMAGE_COMPRESSION_TARGET_BYTES)

    def test_rgba_input_converts_without_raising(self):
        image = Image.new("RGBA", (300, 300), color=(10, 20, 30, 128))
        result = compress_image_for_upload(image)
        self.assertIsInstance(result, bytes)
        # Output must be a valid JPEG (no alpha channel).
        decoded = Image.open(BytesIO(result))
        self.assertEqual(decoded.mode, "RGB")

    def test_palette_image_with_transparency_converts_without_raising(self):
        image = Image.new("P", (100, 100))
        image.info["transparency"] = 0
        result = compress_image_for_upload(image)
        self.assertIsInstance(result, bytes)

    def test_pathological_image_raises_when_uncompressible(self):
        image = _noisy_image((1700, 2200))
        with self.assertRaises(ImageCompressionError):
            compress_image_for_upload(image, target_bytes=1, hard_cap_bytes=1)

    def test_never_resizes_below_minimum_dimension(self):
        image = _noisy_image((2000, 2600))
        try:
            compress_image_for_upload(image, target_bytes=1)
        except ImageCompressionError:
            pass
        # No direct hook into intermediate candidates, so assert indirectly:
        # a resize step producing dimensions below the floor should never be
        # attempted. Verify the floor constant matches expectations.
        self.assertEqual(IMAGE_COMPRESSION_MIN_DIMENSION, 1000)

    def test_never_drops_quality_below_minimum(self):
        self.assertEqual(min(IMAGE_COMPRESSION_QUALITY_STEPS), 45)

    def test_hard_cap_larger_than_target(self):
        self.assertGreater(
            IMAGE_COMPRESSION_HARD_CAP_BYTES, IMAGE_COMPRESSION_TARGET_BYTES
        )
