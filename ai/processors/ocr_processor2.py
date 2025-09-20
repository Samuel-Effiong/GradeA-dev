"""
OCR processor implementation
"""

from typing import Any, Dict

import cv2
import numpy as np
import pytesseract
from PIL import Image


class OCRProcessor:
    def __init__(self):
        self.preprocessing_steps = [self._denoise, self._binarize, self._deskew]

    def process_image(self, image_path: str) -> str:
        """
        Process an image through OCR with preprocessing
        """
        # Load image
        image = cv2.imread(image_path)

        # Apply preprocessing steps
        for step in self.preprocessing_steps:
            image = step(image)

        # Convert to PIL Image for tesseract
        pil_image = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))

        # Perform OCR
        text = pytesseract.image_to_string(pil_image)

        return text

    def _denoise(self, image: np.ndarray) -> np.ndarray:
        """Remove noise from image"""
        return cv2.fastNlMeansDenoisingColored(image, None, 10, 10, 7, 21)

    def _binarize(self, image: np.ndarray) -> np.ndarray:
        """Convert image to binary (black and white)"""
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        return cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]

    def _deskew(self, image: np.ndarray) -> np.ndarray:
        """Correct image skew"""
        coords = np.column_stack(np.where(image > 0))
        angle = cv2.minAreaRect(coords)[-1]
        if angle < -45:
            angle = 90 + angle
        (h, w) = image.shape[:2]
        center = (w // 2, h // 2)
        M = cv2.getRotationMatrix2D(center, angle, 1.0)
        rotated = cv2.warpAffine(
            image, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
        )
        return rotated
