from pathlib import Path

import fitz
from django.core.files.uploadedfile import UploadedFile

# from docutils.transforms.universal import Validate

# from ai_processor.services import ai_processor

# from assignments.models import Assignment


class PDFService:
    """
    Service class for extracting structured data from assignment PDFs
    """

    def __init__(self, uploaded_file: UploadedFile) -> None:
        self.uploaded_file = uploaded_file
        self.extracted_data = {
            "title": "",
            "questions": [],
            "page_count": 0,
        }

    def extract(self) -> dict:
        """
        Extract data from the uploaded pdf
        """

        if self.uploaded_file.content_type != "application/pdf":
            raise ValueError("Unsupported file format. Only PDF is supported.")
        else:
            self.__process_pdf()
        return self.extracted_data

    def __process_pdf(self):
        """
        Process the PDF using fitz (PyMuPDF) to extract data from the UploadedFile object.
        """
        try:
            pdf_bytes = self.uploaded_file.read()
            pdf_document = fitz.open(stream=pdf_bytes, filetype="pdf")

            self.extracted_data["page_count"] = pdf_document.page_count

            full_text = ""

            for page_number in range(pdf_document.page_count):
                page = pdf_document.load_page(page_number)
                full_text += page.get_text().strip()

            # Use the filename for the title
            self.extracted_data["title"] = Path(self.uploaded_file.name).stem
            self.extracted_data["questions"] = full_text

            pdf_document.close()
        except Exception as e:
            raise ValueError(f"Something went wrong: {str(e)}") from e
