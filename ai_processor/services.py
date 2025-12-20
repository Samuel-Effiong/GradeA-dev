import json
from io import BytesIO

import fitz
import numpy as np
from django.conf import settings
from django.core.files.uploadedfile import UploadedFile
from environ import Env
from openai import OpenAI
from paddleocr import PaddleOCR
from pdf2image import convert_from_bytes

# from PIL import Image
from pytesseract import pytesseract

from ai_processor.tools import encode_image, perform_search
from ai_processor.validators import logger

env = Env()
env.read_env(".env")

OPENROUTER_API_KEY: str = env.str(
    "OPENROUTER_API_KEY",
)

# PERSONAL_OPENROUTER = env.str("PERSONAL_OPENROUTER")
#
# DEEPSEEK_API_KEY: str = env.str(
#     "DEEPSEEK_API_KEY",
# )
#
# HF_TOKEN_API_KEY: str = env.str(
#     "HF_TOKEN_API_KEY",
# )


with open("ai_processor/ASSIGNMENT_EXTRACTION_PROMPT_3_HTML.txt", "r") as file:
    ASSIGNMENT_EXTRACTION_PROMPT = file.read()

with open(
    "ai_processor/ASSIGNMENT_EXTRACTION_PROMPT_FROM_UPLOADS_HTML.txt", "r"
) as file:
    ASSIGNMENT_EXTRACTION_PROMPT_FROM_UPLOADS = file.read()

with open("ai_processor/RUBRIC_EXTRACTION_PROMPT.txt", "r") as file:
    RUBRIC_EXTRACTION_PROMPT = file.read()

with open("ai_processor/ANSWERS_EXTRACTION_PROMPT_HTML.txt", "r") as file:
    ANSWERS_EXTRACTION_PROMPT = file.read()

with open("ai_processor/GRADING_ASSIGNMENT_PROMPT_2.txt", "r") as file:
    GRADING_ASSIGNMENT_PROMPT = file.read()

with open("ai_processor/ASSIGNMENT_GENERATION_PROMPT_2.txt", "r") as file:
    GENERATE_ASSIGNMENT_PROMPT = file.read()


tool_schema = [
    {
        "type": "function",
        "function": {
            "name": "fetch_url_content",
            "description": "Fetch the text content from a list of public URLs to get up-to-date or specific "
            "information for the user's request",
            "parameters": {
                "type": "object",
                "properties": {
                    "urls": {
                        "type": "array",
                        "description": "A list of public URLs to fetch content from (e.g., ['https://example.com', "
                        "'https://another.com'])",
                        "items": {
                            "type": "string",
                            "format": "uri",
                            "description": "A single valid public url to fetch content from",
                        },
                        "minItems": 1,
                    }
                },
                "required": ["urls"],
            },
        },
    }
]


class AIProcessor:
    def __init__(self):

        self.client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=OPENROUTER_API_KEY,
        )

        # self.client = OpenAI(
        #     base_url="https://openrouter.ai/api/v1",
        #     api_key=PERSONAL_OPENROUTER,
        # )

        # self.client = OpenAI(
        #     base_url="https://api.deepseek.com",
        #     api_key=DEEPSEEK_API_KEY
        # )
        #
        # self.client = OpenAI(
        #     base_url="https://router.huggingface.co/v1",
        #     api_key=HF_TOKEN_API_KEY
        # )

    def __ai_model(
        self, system_prompt=None, user_prompt=None, messages=None, tool_schemas=None
    ):

        if tool_schemas:
            response = self.client.chat.completions.create(
                extra_headers={
                    "HTTP-Referer": settings.FRONTEND_DOMAIN,
                    "X-Title": "GradeA+",
                },
                model="openai/gpt-5-nano",
                # extra_body={
                #     "models": [
                #         "x-ai/grok-4-fast",
                #         "openai/gpt-5-nano"
                #     ],
                # },
                messages=messages
                or [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                tools=tool_schemas,
                temperature=0.1,
                response_format={"type": "json_object"},
            )
        else:
            response = self.client.chat.completions.create(
                extra_headers={
                    "HTTP-Referer": settings.FRONTEND_DOMAIN,
                    "X-Title": "GradeA+",
                },
                model="openai/gpt-5-nano",
                # extra_body={
                #     "models": [
                #         "x-ai/grok-4-fast",
                #         "openai/gpt-5-nano"
                #     ],
                # },
                messages=messages
                or [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.1,
                response_format={"type": "json_object"},
            )
        return response

    def get_ai_model_function(self):
        return self.__ai_model

    # TODO: Delete function soon
    def __generate_text(self, system_prompt=None, user_prompt=None, messages=None):
        try:
            response = self.client.chat.completions.create(
                extra_headers={
                    "HTTP-Referer": "",  # Optional. Site URL for rankings on openrouter.ai.
                    "X-Title": "GradeA+",  # Optional. Site title for rankings on openrouter.ai.
                },
                model="openai/gpt-5-nano",
                messages=messages
                or [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                    {
                        "role": "system",
                        "content": "if there are urls within the user prompt, use the tool (fetch_url_content) provided"
                        " to you to extract the contents in the url, to gain an uptodate understanding. "
                        "If there are no urls DO NOT USE the tools continue processing the prompt",
                    },
                ],
                tools=tool_schema,
                temperature=0.1,
                response_format={"type": "json_object"},
            )

            message = response.choices[0].message

            tool_calls = message.tool_calls

            if tool_calls:
                tool = message.tool_calls[0]
                tool_name = tool.function.name
                args = json.loads(tool.function.arguments)

                if tool_name == "fetch_url_content":
                    print("Model requested a web search...")
                    query = args["urls"]

                    search_result = perform_search(query)

                    tool_result = {
                        "role": "tool",
                        "tool_call_id": tool.id,
                        "content": json.dumps(search_result),
                    }
                    messages.pop()
                    messages.append(tool_result)

                    # Send the search result back to the model for final reasoning
                    follow_up = self.client.chat.completions.create(
                        extra_headers={
                            "HTTP-Referer": "",  # Optional. Site URL for rankings on openrouter.ai.
                            "X-Title": "GradeA+",  # Optional. Site title for rankings on openrouter.ai.
                        },
                        model="openai/gpt-5-nano",  # deepseek/deepseek-chat-v3.1:free", # openai/gpt-oss-20b:free",
                        # x-ai/grok-4-fast",
                        # extra_body={
                        #     "models": [
                        #         "x-ai/grok-4-fast",
                        #         "openai/gpt-5-nano"
                        #     ],
                        #     # "plugins": [{"id": "web"}],
                        # },
                        messages=messages,
                        response_format={"type": "json_object"},
                    )

                    print("Final answer")
                    content = follow_up.choices[0].message.content
            else:
                content = message.content

            print(f"Received response of length {len(content)}")

            return content

        except Exception as e:
            logger.error(f"Error during AI model: {str(e)}")
            raise Exception(f"Error during AI model: {str(e)}") from Exception

    def create_file(self, uploaded_file):
        # file_bytes = uploaded_file.read()
        # uploaded_file.seek(0)
        encoded_file = encode_image(uploaded_file)
        file_tuple = (uploaded_file.name, encoded_file, uploaded_file.content_type)

        result = self.client.files.upload(file=file_tuple, purpose="user_data")
        return result["id"]

    def extract_assignment(self, text):
        system_prompt = ASSIGNMENT_EXTRACTION_PROMPT

        user_prompt = f"""
Please analyze the following extracted text from an educational assignment and return a JSON

EXTRACTED TEXT:
{text}

IMPORTANT: Return only valid JSON matching the required structure.
Do not include any explanatory text before or after the JSON
"""
        # content = self.__generate_text(system_prompt, user_prompt)

        try:
            response = self.__ai_model(system_prompt, user_prompt)
            content = response.choices[0].message.content

        except Exception as e:
            raise Exception(f"Error during AI model: {str(e)}") from Exception

        try:
            json_data = json.loads(content)
        except json.JSONDecodeError as e:
            logger.error(f"Error decoding JSON: {str(e)}")
            raise Exception(f"Error decoding JSON: {str(e)}") from Exception

        return json_data

        # return self.__generate_text(system_prompt, user_prompt)

    def extract_assignment_image(self, content, upload=False):
        if upload:
            system_prompt = ASSIGNMENT_EXTRACTION_PROMPT_FROM_UPLOADS
        else:
            system_prompt = ASSIGNMENT_EXTRACTION_PROMPT

        try:
            response = self.__ai_model(system_prompt, user_prompt=content)
            content = response.choices[0].message.content
        except Exception as e:
            raise Exception(f"Error during AI model: {str(e)}") from Exception

        try:
            json_data = json.loads(content)
        except json.JSONDecodeError as e:
            logger.error(f"Error decoding JSON: {str(e)}")
            raise Exception(f"Error decoding JSON: {str(e)}") from Exception

        return json_data

    def extract_assignment_with_retry(
        self, content: str | list, max_retries: int = 3, upload=False
    ):
        last_error = None

        for attempt in range(max_retries):
            try:
                return self.extract_assignment_image(content, upload=upload)
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
        # return self.__generate_text(system_prompt, user_prompt)

        # content = self.__generate_text(system_prompt, user_prompt)

        content = self.__ai_model(system_prompt, user_prompt)

        try:
            json_data = json.loads(content)
        except json.JSONDecodeError as e:
            logger.error(f"Error decoding JSON: {str(e)}")
            raise Exception(f"Error decoding JSON: {str(e)}") from Exception
        return json_data

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
        # return self.__generate_text(system_prompt, user_prompt)

        # content = self.__generate_text(system_prompt, user_prompt)
        content = self.__ai_model(system_prompt, user_prompt)

        try:
            json_data = json.loads(content)
        except json.JSONDecodeError as e:
            logger.error(f"Error decoding JSON: {str(e)}")
            raise Exception(f"Error decoding JSON: {str(e)}") from Exception

        return json_data

    def extract_answer_image(self, content):
        system_prompt = ANSWERS_EXTRACTION_PROMPT

        # return self.__generate_text(system_prompt, user_prompt)

        # content = self.__generate_text(system_prompt, user_prompt)
        try:
            response = self.__ai_model(system_prompt, user_prompt=content)
            content = response.choices[0].message.content
        except Exception as e:
            raise Exception(f"Error during AI model: {str(e)}") from Exception

        try:
            json_data = json.loads(content)
        except json.JSONDecodeError as e:
            logger.error(f"Error decoding JSON: {str(e)}")
            raise Exception(f"Error decoding JSON: {str(e)}") from Exception

        return json_data

    def extract_answer_with_retry(self, content, max_retries: int = 3):
        last_error = None

        for attempt in range(max_retries):
            try:
                return self.extract_answer_image(content)
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
        # return self.__generate_text(system_prompt, user_prompt)

        # content = self.__generate_text(system_prompt, user_prompt)
        content = self.__ai_model(system_prompt, user_prompt)
        grade = content.choices[0].message.content

        try:
            json_data = json.loads(grade)
        except json.JSONDecodeError as e:
            logger.error(f"Error decoding JSON: {str(e)}")
            raise Exception(f"Error decoding JSON: {str(e)}") from Exception
        return json_data

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

    def generate_assignment_from_prompt(self, prompt, chat_history=None):
        """Generate an assignment based on the given prompt and chat history."""
        system_prompt = GENERATE_ASSIGNMENT_PROMPT
        messages = [{"role": "system", "content": system_prompt}]

        user_prompt = f"""
Now, respond to the following teacher's instruction using the rules above

>>> USER PROMPT START
{prompt}
>>> USER PROMPT END

        """

        # if chat_history:
        #     messages.extend(chat_history)
        messages.append({"role": "user", "content": user_prompt})
        # messages.append({"role": "user", "content": json_structure})

        additional_instruction = {
            "role": "system",
            "content": "Look for any valid URL(s) within the user prompt and extract all that you can find, "
            "use the tool (fetch_url_content) provided to you to extract the contents in the url, "
            "to gain an uptodate understanding. If there are no urls DO NOT USE the tool",
        }

        messages.append(additional_instruction)

        response = self.__ai_model(messages=messages, tool_schemas=tool_schema)
        message = response.choices[0].message
        tool_calls = message.tool_calls

        if tool_calls:
            tool = message.tool_calls[0]
            tool_name = tool.function.name
            args = json.loads(tool.function.arguments)

            if tool_name == "fetch_url_content":
                print("Model requested a web search...")
                args = args["urls"]

                search_result = perform_search(args)
                tool_result = {
                    "role": "tool",
                    "tool_call_id": tool.id,
                    "content": json.dumps(search_result),
                }
                messages.pop()
                messages.append(message)  # add reasoning to response
                messages.append(tool_result)

                response_2 = self.__ai_model(
                    messages=messages, tool_schemas=tool_schema
                )
                content = response_2.choices[0].message.content
        else:
            content = message.content

        # content = self.__generate_text(messages=messages)

        print(f"Received response of length {len(content)}")

        try:
            json_data = json.loads(content)
        except json.JSONDecodeError as e:
            logger.error(f"Error decoding JSON: {str(e)}")
            raise Exception(f"Error decoding JSON: {str(e)}") from Exception

        return json_data

    def generate_assignment_from_prompt_with_retry(
        self, prompt, chat_history=None, max_retries: int = 3
    ):
        """
        Retry wrapper for generate_assignment_from_prompt
        """

        last_error = None

        for attempt in range(max_retries):
            try:
                return self.generate_assignment_from_prompt(prompt, chat_history)
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

    def extract(self):
        """Extract data from the uploaded pdf"""
        self.clear_extracted_data()

        if self.uploaded_file.content_type != "application/pdf":
            raise ValueError(
                f"Unsupported file type: {self.uploaded_file.content_type}"
            )

        pdf_bytes = self.uploaded_file.read()

        images = convert_from_bytes(pdf_bytes)

        images_byte = []

        for image in images:
            buffered = BytesIO()
            image.save(buffered, format="PNG")
            image_byte = buffered.getvalue()
            encoded_image_byte = encode_image(image_byte=image_byte)
            images_byte.append(encoded_image_byte)

        return images_byte

        # First, try to extract text directly from the PDF
        # self.__extract_text_based(pdf_bytes)

        # If no text was extracted, it's likely a scanned PDF
        # if not self.extracted_data["questions"]:
        #     self.__extract_text_with_ocr(pdf_bytes)
        #
        # self.extracted_data["page_count"] = self.__get_page_count(pdf_bytes)
        # self.extracted_data["title"] = Path(self.uploaded_file.name).stem
        #
        # return self.extracted_data

    def __get_page_count(self, pdf_bytes):
        """Helper to get the number of pages"""
        with fitz.open(stream=pdf_bytes, filetype="pdf") as pdf:
            return pdf.page_count

    def clear_extracted_data(self):
        self.extracted_data = {
            "title": "",
            "questions": "",
            "page_count": 0,
        }

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

            image_byte = []

            for image in images:
                image_byte.append(image.tobytes())
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


ocr_service = OCRService()
pdf_service = PDFService()
ai_processor = AIProcessor()
