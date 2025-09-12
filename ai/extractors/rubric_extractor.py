"""
Rubric extractor implementation
"""

from typing import Any, Dict

import fitz
import pytesseract
from PIL import Image

from .base_extractor import BaseExtractor


class RubricExtractor(BaseExtractor):
    def __init__(self):
        super().__init__()
        self.point_markers = ["points", "pts", "pt"]

    def _extract_from_pdf(self, pdf_path: str) -> Dict[str, Any]:
        doc = fitz.open(pdf_path)
        rubric_items = {}
        total_points = 0

        for page in doc:
            text = page.get_text()
            lines = text.split("\n")

            current_question = None
            for line in lines:
                # Look for point values
                points = self._extract_points(line)
                if points > 0:
                    # Associate points with the previous question
                    if current_question:
                        rubric_items[current_question] = {
                            "points": points,
                            "criteria": line.strip(),
                        }
                        total_points += points
                elif "?" in line:  # This is a question
                    current_question = line.strip()

        return {
            "type": "rubric",
            "rubric_items": rubric_items,
            "total_points": total_points,
            "metadata": {"page_count": len(doc), "source": pdf_path},
        }

    def _extract_from_image(self, image_path: str) -> Dict[str, Any]:
        # Use OCR to extract text
        text = pytesseract.image_to_string(Image.open(image_path))
        rubric_items = {}
        total_points = 0

        lines = text.split("\n")
        current_question = None

        for line in lines:
            # Look for point values
            points = self._extract_points(line)
            if points > 0:
                # Associate points with the previous question
                if current_question:
                    rubric_items[current_question] = {
                        "points": points,
                        "criteria": line.strip(),
                    }
                    total_points += points
            elif "?" in line:  # This is a question
                current_question = line.strip()

        return {
            "type": "rubric",
            "rubric_items": rubric_items,
            "total_points": total_points,
            "metadata": {"source": image_path},
        }

    def _extract_points(self, text: str) -> int:
        """Extract point value from text"""
        words = text.lower().split()
        for i, word in enumerate(words):
            if any(marker in word for marker in self.point_markers):
                try:
                    # Look for number before the point marker
                    return int(words[i - 1])
                except (ValueError, IndexError):
                    continue
        return 0

    def _validate_output(self, data: Dict[str, Any]) -> bool:
        required_fields = ["type", "rubric_items", "total_points", "metadata"]
        if not all(field in data for field in required_fields):
            return False

        # Validate rubric items
        for item, details in data["rubric_items"].items():
            if not all(field in details for field in ["points", "criteria"]):
                return False

        return True
