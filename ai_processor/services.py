import json
from pathlib import Path

import fitz
import numpy as np
from django.core.files.uploadedfile import UploadedFile
from environ import Env
from openai import OpenAI
from paddleocr import PaddleOCR
from pdf2image import convert_from_bytes

# from PIL import Image
from pytesseract import pytesseract

from ai_processor.validators import logger

env = Env()
env.read_env(".env")

OPENROUTER_API_KEY: str = env.str(
    "OPENROUTER_API_KEY",
    default="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",
)


with open("ai_processor/ASSIGNMENT_EXTRACTION_PROMPT.txt", "r") as file:
    ASSIGNMENT_EXTRACTION_PROMPT = file.read()

with open("ai_processor/RUBRIC_EXTRACTION_PROMPT.txt", "r") as file:
    RUBRIC_EXTRACTION_PROMPT = file.read()

with open("ai_processor/ANSWERS_EXTRACTION_PROMPT.txt", "r") as file:
    ANSWERS_EXTRACTION_PROMPT = file.read()

with open("ai_processor/GRADING_ASSIGNMENT_PROMPT.txt", "r") as file:
    GRADING_ASSIGNMENT_PROMPT = file.read()


class AIProcessor:
    def __init__(self):

        self.client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=OPENROUTER_API_KEY,
        )

    def __generate_text(self, system_prompt, user_prompt):
        try:
            response = self.client.chat.completions.create(
                extra_headers={
                    "HTTP-Referer": "",  # Optional. Site URL for rankings on openrouter.ai.
                    "X-Title": "",  # Optional. Site title for rankings on openrouter.ai.
                },
                model="openai/gpt-oss-120b:free",
                extra_body={
                    "models": [
                        "openrouter/sonoma-sky-alpha",
                        "deepseek/deepseek-chat-v3.1",
                        "google/gemma-3-27b-it",
                    ],
                },
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.1,
                response_format={"type": "json_object"},
            )

            content = response.choices[0].message.content
            print(f"Recieved response of lenght {len(content)}")

            try:
                json_data = json.loads(content)
            except json.JSONDecodeError as e:
                print("Error decoding JSON")
                raise Exception(f"Error decoding JSON: {str(e)}") from Exception

            return json_data
        except Exception as e:
            logger.error(f"Error during extraction: {str(e)}")
            raise Exception(f"Assignment extraction failed: {str(e)}") from Exception

    def extract_assignment(self, text):
        system_prompt = ASSIGNMENT_EXTRACTION_PROMPT

        user_prompt = f"""
Please analyze the following extracted text from an educational assignment and return a JSON

EXTRACTED TEXT:
{text}

IMPORTANT: Return only valid JSON matching the required structure.
Do not include any explanatory text before or after the JSON
"""
        return self.__generate_text(system_prompt, user_prompt)

    def extract_assignment_with_retry(self, text: str, max_retries: int = 3):
        last_error = None

        for attempt in range(max_retries):
            try:
                return self.extract_assignment(text)
            except Exception as e:
                last_error = e
                logger.warning(f"Attempt {attempt + 1} failed: {str(e)}")

                if attempt < max_retries - 1:
                    logger.info("Retrying...")

        raise Exception(f"All {max_retries} attempts failed. Last error: {last_error}")

    def extract_rubric(self, text):
        system_prompt = RUBRIC_EXTRACTION_PROMPT
        user_prompt = f"""
Please analyze the following extracted text from an educational rubric and return a JSON

EXTRACTED TEXT:
{text}

IMPORTANT: Return only valid JSON matching the required structure.
Do not include any explanatory text before or after the JSON
"""
        return self.__generate_text(system_prompt, user_prompt)

    def extract_rubric_with_retry(self, text: str, max_retries: int = 3):
        last_error = None

        for attempt in range(max_retries):
            try:
                return self.extract_rubric(text)
            except Exception as e:
                last_error = e
                logger.warning(f"Attempt {attempt + 1} failed: {str(e)}")

                if attempt < max_retries - 1:
                    logger.info("Retrying...")

        raise Exception(f"All {max_retries} attempts failed. Last error: {last_error}")

    def extract_answer(self, text):
        system_prompt = ANSWERS_EXTRACTION_PROMPT

        user_prompt = f"""
Please analyze the following extracted text from an educational assignment and answers and return a JSON

EXTRACTED TEXT:
{text}

IMPORTANT: Return only valid JSON matching the required structure.
Do not include any explanatory text before or after the JSON

"""
        return self.__generate_text(system_prompt, user_prompt)

    def extract_answer_with_retry(self, text: str, max_retries: int = 3):
        last_error = None

        for attempt in range(max_retries):
            try:
                return self.extract_answer(text)
            except Exception as e:
                last_error = e
                logger.warning(f"Attempt {attempt + 1} failed: {str(e)}")

                if attempt < max_retries - 1:
                    logger.info("Retrying...")
        raise Exception(f"All {max_retries} attempts failed. Last error: {last_error}")

    def grade_student_submission(self, rubric_json, answer_json):
        system_prompt = GRADING_ASSIGNMENT_PROMPT

        user_prompt = f"""
You are given the following rubric and student answers.
Use the rubric to grade each student answer, assign points, and provide constructive feedback.
Return the results strictly in the JSON grading format shown in the background instructions.

### Rubric JSON
{rubric_json}

### Student Answers JSON
{answer_json}

Now, grade the student answers based on the rubric.
Make sure to:
1. Match each answer with its question in the rubric.
2. Award points according to the closest scoring level.
3. Provide detailed feedback for each answer.
4. Calculate the total score and overall feedback.
"""
        return self.__generate_text(system_prompt, user_prompt)

    def extract_grade_with_retry(self, rubric_json, answer_json, max_retries: int = 3):
        last_error = None

        for attempt in range(max_retries):
            try:
                return self.grade_student_submission(rubric_json, answer_json)
            except Exception as e:
                last_error = e
                logger.warning(f"Attempt {attempt + 1} failed: {str(e)}")

                if attempt < max_retries - 1:
                    logger.info("Retrying...")

        raise Exception(f"All {max_retries} attempts failed. Last error: {last_error}")


class PDFService:
    def __init__(self, uploaded_file: UploadedFile = None):
        # self.ocr_service = OCRService()

        self.uploaded_file = uploaded_file

        self.extracted_data = {
            "title": "",
            "questions": "",
            "page_count": 0,
        }

    def set_uploaded_file(self, uploaded_file: UploadedFile):
        self.uploaded_file = uploaded_file

    def extract(self) -> dict:
        """Extract data from the uploaded pdf"""

        if self.uploaded_file.content_type != "application/pdf":
            raise ValueError(
                f"Unsupported file type: {self.uploaded_file.content_type}"
            )

        pdf_bytes = self.uploaded_file.read()

        # First, try to extract text directly from the PDF
        # self.__extract_text_based(pdf_bytes)

        # If no text was extracted, it's likely a scanned PDF
        if not self.extracted_data["questions"]:
            self.__extract_text_with_ocr(pdf_bytes)

        self.extracted_data["page_count"] = self.__get_page_count(pdf_bytes)
        self.extracted_data["title"] = Path(self.uploaded_file.name).stem

        return self.extracted_data

    def __get_page_count(self, pdf_bytes):
        """Helper to get the number of pages"""
        with fitz.open(stream=pdf_bytes, filetype="pdf") as pdf:
            return pdf.page_count

    def __extract_text_based(self, pdf_bytes):
        """Extract text from a PDF that is text-based or has a text layer"""

        try:
            with fitz.open(stream=pdf_bytes, filetype="pdf") as pdf:
                full_text = ""
                for page in pdf:
                    full_text += page.get_text().strip()

                self.extracted_data["questions"] = full_text
        except Exception as e:
            raise ValueError(f"Something went wrong: {e}") from Exception

    def __extract_text_with_ocr(self, pdf_bytes):
        """Extract text from a PDF that is scanned"""

        try:
            # Convert PDF pages to a list of PIL Image objects from the in-memory stream
            images = convert_from_bytes(pdf_bytes, dpi=200)

            full_text = ""

            for image in images:
                text = ocr_service.extract_with_paddle(image)
                full_text += text

            self.extracted_data["questions"] = full_text
        except Exception as e:
            raise ValueError(f"Something went wrong: {e}") from Exception


class OCRService:
    _paddle_ocr_model: PaddleOCR = None

    def __init__(self):
        if OCRService._paddle_ocr_model is None:
            from paddleocr import PaddleOCR

            OCRService._paddle_ocr_model = PaddleOCR(
                use_doc_orientation_classify=True,
                use_doc_unwarping=True,
                use_textline_orientation=True,
            )

    def extract_with_paddle(self, image):
        model = OCRService._paddle_ocr_model
        img_np = np.array(image.convert("RGB"))
        result = model.predict(img_np)

        text = ""
        for res in result:
            text = res.json["res"]["rec_texts"]
        return "\n".join(text)

    def extract_with_pytessaract(self, image):
        """

        :param image: PIL Image
        :return:
        """
        text = pytesseract.image_to_string(image)
        return text


_ocr_instance = None
_pdf_instance = None
_ai_processor_instance = None


def get_ocr_service():
    global _ocr_instance
    if _ocr_instance is None:
        _ocr_instance = OCRService()
    return _ocr_instance


def get_pdf_service(uploaded_file: UploadedFile = None):
    """Get PDFService instance - can be created per request"""
    if uploaded_file:
        return PDFService(uploaded_file)

    global _pdf_instance
    if _pdf_instance is None:
        _pdf_instance = PDFService()
    return _pdf_instance


def get_ai_processor():
    """Get singleton AIProcessor instance"""
    global _ai_processor_instance
    if _ai_processor_instance is None:
        _ai_processor_instance = AIProcessor()
    return _ai_processor_instance


ocr_service = OCRService()
pdf_service = PDFService()
ai_processor = AIProcessor()
