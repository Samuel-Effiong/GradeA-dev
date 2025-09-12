"""
Answer extractor implementation
"""

from typing import Any, Dict

import fitz
import pytesseract
from PIL import Image

from .base_extractor import BaseExtractor


class AnswerExtractor(BaseExtractor):
    def __init__(self):
        super().__init__()
        self.answer_box_height = 100  # pixels

    def _extract_from_pdf(self, pdf_path: str) -> Dict[str, Any]:
        doc = fitz.open(pdf_path)
        answers = {}
        student_info = {"name": "", "section": ""}

        for page_num, page in enumerate(doc, 1):
            # Extract text blocks
            blocks = page.get_text("blocks")

            # Sort blocks top to bottom, left to right
            blocks.sort(key=lambda b: (b[1], b[0]))

            for block in blocks:
                text = block[4].strip()

                # Extract student info
                if "Name:" in text:
                    student_info["name"] = text.replace("Name:", "").strip()
                elif "Section:" in text:
                    student_info["section"] = text.replace("Section:", "").strip()

                # Look for answers (typically in boxes/regions)
                # This is a simplified approach - you might need more sophisticated box detection
                if not any(
                    keyword in text.lower()
                    for keyword in ["name:", "section:", "instruction"]
                ):
                    answers[f"answer_{len(answers) + 1}"] = text

        return {
            "type": "answer",
            "student_info": student_info,
            "answers": answers,
            "metadata": {"page_count": len(doc), "source": pdf_path},
        }

    def _extract_from_image(self, image_path: str) -> Dict[str, Any]:
        # Use OCR to extract text
        text = pytesseract.image_to_string(Image.open(image_path))
        answers = {}
        student_info = {"name": "", "section": ""}

        lines = text.split("\n")
        answer_mode = False
        current_answer = ""

        for line in lines:
            if "Name:" in line:
                student_info["name"] = line.replace("Name:", "").strip()
            elif "Section:" in line:
                student_info["section"] = line.replace("Section:", "").strip()
            elif len(line.strip()) > 0 and not any(
                keyword in line.lower() for keyword in ["instruction"]
            ):
                answers[f"answer_{len(answers) + 1}"] = line.strip()

        return {
            "type": "answer",
            "student_info": student_info,
            "answers": answers,
            "metadata": {"source": image_path},
        }

    def _validate_output(self, data: Dict[str, Any]) -> bool:
        required_fields = ["type", "student_info", "answers", "metadata"]
        if not all(field in data for field in required_fields):
            return False

        # Validate student_info
        if not all(field in data["student_info"] for field in ["name", "section"]):
            return False

        return True
