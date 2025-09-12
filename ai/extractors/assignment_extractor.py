"""
Assignment extractor implementation
"""

from typing import Any, Dict

import fitz
import pytesseract
from PIL import Image

from .base_extractor import BaseExtractor


class AssignmentExtractor(BaseExtractor):
    def __init__(self):
        super().__init__()
        self.question_markers = ["?", "Question", "Problem"]

    def _extract_from_pdf(self, pdf_path: str) -> Dict[str, Any]:
        # Open the PDF
        doc = fitz.open(pdf_path)
        questions = []
        instructions = ""

        for page in doc:
            text = page.get_text()
            # Process text to extract questions and instructions
            lines = text.split("\n")
            for line in lines:
                if any(marker in line for marker in self.question_markers):
                    questions.append(line.strip())
                elif "instruction" in line.lower():
                    instructions += line.strip() + "\n"

        return {
            "type": "assignment",
            "instructions": instructions.strip(),
            "questions": questions,
            "metadata": {"page_count": len(doc), "source": pdf_path},
        }

    def _extract_from_image(self, image_path: str) -> Dict[str, Any]:
        # Use OCR to extract text from image
        text = pytesseract.image_to_string(Image.open(image_path))
        questions = []
        instructions = ""

        lines = text.split("\n")
        for line in lines:
            if any(marker in line for marker in self.question_markers):
                questions.append(line.strip())
            elif "instruction" in line.lower():
                instructions += line.strip() + "\n"

        return {
            "type": "assignment",
            "instructions": instructions.strip(),
            "questions": questions,
            "metadata": {"source": image_path},
        }

    def _validate_output(self, data: Dict[str, Any]) -> bool:
        required_fields = ["type", "instructions", "questions", "metadata"]
        return all(field in data for field in required_fields)
