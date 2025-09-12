"""
Base extractor class for all AI extractors
"""

import json
from abc import ABC, abstractmethod
from typing import Any, Dict, Union

import fitz
from PIL import Image


class BaseExtractor(ABC):
    def __init__(self):
        self.supported_formats = [".pdf", ".png", ".jpg", ".jpeg"]

    def extract(self, file_path: str) -> Dict[str, Any]:
        """
        Main extraction method that handles both PDF and image inputs
        """
        file_ext = file_path.lower()[-4:]
        if file_ext not in self.supported_formats:
            raise ValueError(f"Unsupported file format: {file_ext}")

        if file_ext == ".pdf":
            return self._extract_from_pdf(file_path)
        else:
            return self._extract_from_image(file_path)

    @abstractmethod
    def _extract_from_pdf(self, pdf_path: str) -> Dict[str, Any]:
        """Extract information from PDF file"""
        pass

    @abstractmethod
    def _extract_from_image(self, image_path: str) -> Dict[str, Any]:
        """Extract information from image file"""
        pass

    def _validate_output(self, data: Dict[str, Any]) -> bool:
        """Validate extracted data against schema"""
        pass
