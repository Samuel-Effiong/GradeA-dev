import base64
import json
import math
import uuid
from io import BytesIO
from typing import Any, Dict, Optional

import fitz
import tiktoken

# import numpy as np
from django.conf import settings
from django.core.files.uploadedfile import UploadedFile
from django.db import transaction
from environ import Env
from openai import OpenAI

# from paddleocr import PaddleOCR
from pdf2image import convert_from_bytes
from PIL import Image

# from ai_processor.models import ChatMessage, ChatSession
from ai_processor.tools import encode_image, perform_search
from ai_processor.validators import logger
from billing.errors import InsufficientCreditsError
from billing.services import AnalyticsService
from classrooms.models import StudentCourse
from users.models import UserTypes

# from billing.services import SubscriptionService

# from PIL import Image
# from pytesseract import pytesseract


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

AI_CONFIDENCE_THRESHOLD = 80

with open("ai_processor/ASSIGNMENT_EXTRACTION_PROMPT_4_PROSE.txt", "r") as file:
    ASSIGNMENT_EXTRACTION_PROMPT = file.read()

with open(
    "ai_processor/ASSIGNMENT_EXTRACTION_PROMPT_FROM_UPLOADS_HTML.txt", "r"
) as file:
    ASSIGNMENT_EXTRACTION_PROMPT_FROM_UPLOADS = file.read()

with open("ai_processor/RUBRIC_EXTRACTION_PROMPT.txt", "r") as file:
    RUBRIC_EXTRACTION_PROMPT = file.read()

with open("ai_processor/ANSWERS_EXTRACTION_PROMPT_HTML_4.txt", "r") as file:
    ANSWERS_EXTRACTION_PROMPT = file.read()

with open("ai_processor/GRADING_ASSIGNMENT_PROMPT_2.txt", "r") as file:
    GRADING_ASSIGNMENT_PROMPT = file.read()

with open("ai_processor/ASSIGNMENT_GENERATION_PROMPT_2.txt", "r") as file:
    GENERATE_ASSIGNMENT_PROMPT = file.read()

with open("ai_processor/GRADE_FORMATTER_2.txt", "r") as file:
    GRADE_FORMATTER = file.read()

with open("ai_processor/STUDENT_SUMMARY_PROMPT.txt", "r") as file:
    STUDENT_SUMMARY_PROMPT = file.read()

CHUNKED_EXTRACTION_PAGE_THRESHOLD = 4
CHUNK_SIZE = 2

PROSEMIRROR_CHUNK_THRESHOLD = 6000
PROSEMIRROR_TOKEN_BUDGET_PER_CHUNK = 3000


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
        self,
        system_prompt=None,
        user_prompt=None,
        messages=None,
        tool_schemas=None,
        respond_format=True,
    ):
        main_model = "x-ai/grok-4.1-fast"
        sub_models = ["openai/gpt-5-nano", "google/gemini-3-flash-preview"]

        if tool_schemas:
            response = self.client.chat.completions.create(
                extra_headers={
                    "HTTP-Referer": settings.FRONTEND_DOMAIN,
                    "X-Title": "GradeA+",
                },
                model=main_model,
                extra_body={
                    "models": sub_models,
                },
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
                model=main_model,
                extra_body={"models": sub_models},
                messages=messages
                or [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.1,
                response_format={"type": "json_object"} if respond_format else None,
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

    def extract_assignment(self, user, text):
        system_prompt = ASSIGNMENT_EXTRACTION_PROMPT

        try:
            doc = json.loads(text)

            if isinstance(doc, dict) and doc.get("type") == "doc":
                encoding = tiktoken.get_encoding("cl100k_base")
                token_count = len(encoding.encode(text))

                if token_count > PROSEMIRROR_CHUNK_THRESHOLD:
                    logger.info(
                        f"[Chunked Extraction] ProseMirror document is {token_count} tokens "
                        f"(threshold: {PROSEMIRROR_CHUNK_THRESHOLD}). "
                        f"Switching to chunked extraction."
                    )
                    return self._extract_prosemirror_chunked(user, doc)

        except (json.JSONDecodeError, TypeError):
            pass

        user_prompt = f"""
Please analyze the following extracted text from an educational assignment and return a JSON

EXTRACTED TEXT:
{text}

IMPORTANT: Return only valid JSON matching the required structure.
Do not include any explanatory text before or after the JSON
"""
        # content = self.__generate_text(system_prompt, user_prompt)

        try:
            # response = self.__ai_model(system_prompt, user_prompt)

            response = self.execute_graded_task(
                user=user,
                feature="Assignment Extraction",
                task_type="extract_assignment",
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )

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

    def extract_assignment_image(self, user, content, upload=False):
        if upload:
            system_prompt = ASSIGNMENT_EXTRACTION_PROMPT_FROM_UPLOADS
        else:
            system_prompt = ASSIGNMENT_EXTRACTION_PROMPT

        try:
            if "raw_input" in content[0]:
                raw_input = content[0]["raw_input"]
                doc = json.loads(raw_input)

                if isinstance(doc, dict) and doc.get("type") == "doc":
                    encoding = tiktoken.get_encoding("cl100k_base")
                    token_count = len(encoding.encode(raw_input))

                    if token_count > PROSEMIRROR_CHUNK_THRESHOLD:
                        logger.info(
                            f"[Chunked Extraction] ProseMirror document is {token_count} tokens "
                            f"(threshold: {PROSEMIRROR_CHUNK_THRESHOLD}). "
                            f"Switching to chunked extraction."
                        )
                        return self._extract_prosemirror_chunked(user, doc)
        except (json.JSONDecodeError, TypeError) as e:
            logger.error(f"Error decoding JSON: {str(e)}")
            pass

        try:
            response = self.execute_graded_task(
                user=user,
                feature="Assignment Extraction",
                task_type="extract_assignment",
                system_prompt=system_prompt,
                user_prompt=content,
            )

            content = response.choices[0].message.content
        except Exception as e:
            raise Exception(f"Error during AI model: {str(e)}") from Exception

        try:
            json_data = json.loads(content)
        except json.JSONDecodeError as e:
            logger.error(f"Error decoding JSON: {str(e)}")
            raise Exception(f"Error decoding JSON: {str(e)}") from Exception

        return json_data

    def _split_into_chunks(self, items: list, chunk_size: int) -> list:
        chunks = [items[i : i + chunk_size] for i in range(0, len(items), chunk_size)]

        return chunks

    # def _extract_chunk_with_retry(self, user, page_items: list, ):

    def _split_prosemirror_into_chunks(
        self, doc: dict, token_budget: int = PROSEMIRROR_TOKEN_BUDGET_PER_CHUNK
    ) -> list:
        encoding = tiktoken.get_encoding("cl100k_base")
        top_level_nodes = doc.get("content", [])

        chunks = []
        current_chunk_nodes = []
        current_token_count = 0

        for node in top_level_nodes:
            node_tokens = len(encoding.encode(json.dumps(node)))

            # if adding this node would exceed the budget AND we already have
            # nodes accumulated, close the current chunk first

            if current_token_count + node_tokens > token_budget and current_chunk_nodes:
                chunks.append({"type": "doc", "content": current_chunk_nodes})
                current_chunk_nodes = []
                current_token_count = 0

            current_chunk_nodes.append(node)
            current_token_count += node_tokens

        # Flush any remaining nodes as the final chunk
        if current_chunk_nodes:
            chunks.append({"type": "doc", "content": current_chunk_nodes})

        return chunks

    def _extract_prosemirror_chunked(self, user, doc: dict):
        chunks = self._split_prosemirror_into_chunks(doc)

        total_chunks = len(chunks)

        logger.info(
            f"[Chunked Extraction] ProseMirror document -> "
            f"{total_chunks} token-bounded chunks."
        )

        merged_questions = []
        base_result = None

        for chunk_index, chunk_doc in enumerate(chunks):
            logger.info(
                f"[Chunked Extraction] Processing ProseMirror chunk "
                f"{chunk_index + 1} / {total_chunks}..."
            )

            # Build a context note so the AI knows this is a partial document
            # and where question numbering should continue from

            chunk_note = (
                f"NOTE: You are processing part {chunk_index + 1} of {total_chunks} "
                f"of a large assignment document that has been split for processing. "
                f"Extract ONLY the questions visible in this portion. "
                f"Continue sequential question numbering from question "
                f"{len(merged_questions) + 1}. "
                f"Do not repeat questions from previous parts. "
                f"Your ONLY job is to extract every question in this portion fully and correctly. "
                f"You must be extremely meticulous and thorough. "
                f"Do not skip any questions. "
                f"Rubrics, model answers, options -- all the same rules apply as normal. "
                f"Do not rush or abbreviate to 'save space' and do NOT skip any question "
                f"visible in this portion. "
            )

            # Only the first chunk should emit title and instructions
            # All other chunks should set those fields to empty strings

            if chunk_index == 0:
                chunk_note += (
                    "This is the FIRST part -- extract the assignment title and "
                    "instructions as normal"
                )
            else:
                chunk_note += (
                    "This is NOT the first part -- set title and instructions to "
                    "empty strings. they were already extracted."
                )

            # Combine context note + serialized ProseMirror chunk as plain text
            chunk_content = [
                {
                    "type": "text",
                    "text": (
                        chunk_note
                        + "\n\nPROSEMIRROR DOCUMENT CHUNK:\n"
                        + json.dumps(chunk_doc, indent=2)
                        + "\n\nEND OF CHUNK"
                    ),
                }
            ]

            last_chunk_error = None
            chunk_result = None

            # Retry each individual chuk up to 3 times before failing
            for attempt in range(3):
                try:
                    response = self.execute_graded_task(
                        user=user,
                        feature="Assignment Extraction",
                        task_type="extract_assignment",
                        system_prompt=ASSIGNMENT_EXTRACTION_PROMPT,
                        user_prompt=chunk_content,
                    )
                    raw = response.choices[0].message.content

                    # Strip markdown fences in case the model wraps output
                    raw = raw.strip()

                    if raw.startswith("```json"):
                        raw = raw[7:]
                    elif raw.startswith("```"):
                        raw = raw[3:]
                    if raw.endswith("```"):
                        raw = raw[:-3]

                    raw = raw.strip()

                    chunk_result = json.loads(raw)
                    break
                except json.JSONDecodeError as e:
                    last_chunk_error = e
                    logger.warning(
                        f"[Chunked Extraction] ProseMirror chunk {chunk_index + 1}, "
                        f"attempt {attempt + 1}: JSON decode failed - {str(e)}"
                    )
                except Exception as e:
                    logger.warning(
                        f"[Chunked Extraction] ProseMirror chunk {chunk_index + 1}, "
                        f"attempt {attempt + 1}: AI call failed — {str(e)}"
                    )

            if chunk_result is None:
                raise Exception(
                    f"[Chunked Extraction] ProseMirror chunk {chunk_index + 1} failed "
                    f"after 3 attempts. Last error: {last_chunk_error}"
                )

            # Store the first chunk's result as the base for metadata fields
            if base_result is None:
                base_result = chunk_result

            chunk_questions = chunk_result.get("questions", [])
            merged_questions.extend(chunk_questions)

        if base_result is None:
            raise Exception(
                "[Chunked Extraction] No ProseMirror chunks were successfully processed."
            )

        # Re-index all question numbers globally (1, 2, 3 ... N)
        for idx, question in enumerate(merged_questions, start=1):
            question["question_number"] = idx

        # Assemble the final merged result
        base_result["questions"] = merged_questions
        base_result["question_count"] = len(merged_questions)
        base_result["total_points"] = sum(q.get("points", 0) for q in merged_questions)

        # Determine overall assignment type from merged questions
        types_present = {q.get("question_type") for q in merged_questions}
        if len(types_present) > 1:
            base_result["assignment_type"] = "HYBRID"
        elif types_present:
            base_result["assignment_type"] = types_present.pop()

        logger.info(
            f"[Chunked Extraction] ProseMirror done. Merged {len(merged_questions)} "
            f"questions from {total_chunks} chunks."
        )

        return base_result

    def _extract_assignment_chunked(
        self, user, image_contents: list, upload=False, pages_per_chunk: int = 4
    ):
        """
        Splits a large list of page images into smaller batches and extracts
        assignment data from each batch independently, then merges the results.

        This solves two problems with large assignments (30+ questions):
        1. Truncated output - AI hits output token limits and stops mid-JSON.
        2. JSON parse errors - truncated JSON is malformed and unreadable.

        The questions array from each chunk is merged sequentially. Question
        numbers are re-indexed globally to ensure correct ordering.
        Metadata (title, instructions, total_points, etc.) is taken from the
        first chunk and updated once all questions are merged.

        Args:
            user: The authenticated user (for billing).
            image_contents: A flat list of image_url content items, one per page.
            upload: Whether to use the uploads prompt or the prose prompt.
            pages_per_chunk: How many pages to process per AI call (default 4).

        Returns:
            dict: The merged assignment JSON with all questions.
        """
        if upload:
            system_prompt = ASSIGNMENT_EXTRACTION_PROMPT_FROM_UPLOADS
        else:
            system_prompt = ASSIGNMENT_EXTRACTION_PROMPT

        # Split image list into chunks of `pages_per_chunk`
        chunks = self._split_into_chunks(image_contents, CHUNK_SIZE)
        # chunks = [
        #     image_contents[i : i + pages_per_chunk]
        #     for i in range(0, len(image_contents), pages_per_chunk)
        # ]

        logger.info(
            f"[Chunked Extraction] {len(image_contents)} pages → "
            f"{len(chunks)} chunks of up to {pages_per_chunk} pages each."
        )

        merged_questions = []
        base_result = None

        for chunk_index, chunk in enumerate(chunks):
            logger.info(
                f"[Chunked Extraction] Processing chunk {chunk_index + 1}/{len(chunks)}..."
            )

            # Build a context note so the AI knows this is a partial document
            chunk_note = (
                f"NOTE: You are processing pages {chunk_index * pages_per_chunk + 1} to "
                f"{min((chunk_index + 1) * pages_per_chunk, len(image_contents))} of a "
                f"{len(image_contents)}-page document. Extract ONLY the questions visible "
                f"on these pages. Continue sequential question numbering from question "
                f"{len(merged_questions) + 1}. Do not repeat questions from previous pages."
                f"Your ONLY job is to extract every question on these pages fully and correctly"
                f"Rubrics, model answers, options - all the same rules apply as normal"
                f"Do not rush or abbreviate to 'save space' and do NOT skip any question visible in these pages"
            )

            chunk_content = [
                {"type": "text", "text": chunk_note},
                *chunk,
            ]

            last_chunk_error: Optional[Exception] = None
            chunk_result: Optional[Dict[str, Any]] = None

            # Retry each individual chunk up to 3 times before failing
            for attempt in range(3):
                try:
                    response = self.execute_graded_task(
                        user=user,
                        feature="Assignment Extraction",
                        task_type="extract_assignment",
                        system_prompt=system_prompt,
                        user_prompt=chunk_content,
                    )
                    raw = response.choices[0].message.content

                    # Clean the response in case the model wraps it in markdown blocks
                    raw = raw.strip()
                    if raw.startswith("```json"):
                        raw = raw[7:]
                    elif raw.startswith("```"):
                        raw = raw[3:]
                    if raw.endswith("```"):
                        raw = raw[:-3]
                    raw = raw.strip()

                    chunk_result = json.loads(raw)
                    break
                except json.JSONDecodeError as e:
                    last_chunk_error = e
                    logger.warning(
                        f"[Chunked Extraction] Chunk {chunk_index + 1}, attempt {attempt + 1}: "
                        f"JSON decode failed — {str(e)}"
                    )
                except Exception as e:
                    last_chunk_error = e
                    logger.warning(
                        f"[Chunked Extraction] Chunk {chunk_index + 1}, attempt {attempt + 1}: "
                        f"AI call failed — {str(e)}"
                    )

            if chunk_result is None:
                raise Exception(
                    f"[Chunked Extraction] Chunk {chunk_index + 1} failed after 3 attempts. "
                    f"Last error: {last_chunk_error}"
                )

            # Store the first chunk's result as the base for metadata fields
            if base_result is None:
                base_result = chunk_result

            chunk_questions = chunk_result.get("questions", [])
            merged_questions.extend(chunk_questions)

        if base_result is None:
            raise Exception(
                "[Chunked Extraction] No chunks were successfully processed."
            )

        # Re-index all question numbers globally (1, 2, 3 ... N)
        for idx, question in enumerate(merged_questions, start=1):
            question["question_number"] = idx

        # Assemble the final merged result
        base_result["questions"] = merged_questions
        base_result["question_count"] = len(merged_questions)
        base_result["total_points"] = sum(q.get("points", 0) for q in merged_questions)

        # Determine overall assignment type from merged questions
        types_present = {q.get("question_type") for q in merged_questions}
        if len(types_present) > 1:
            base_result["assignment_type"] = "HYBRID"
        elif types_present:
            base_result["assignment_type"] = types_present.pop()

        logger.info(
            f"[Chunked Extraction] Done. Merged {len(merged_questions)} questions "
            f"from {len(chunks)} chunks."
        )

        return base_result

    def extract_assignment_with_retry(
        self,
        user,
        content: str | list,
        max_retries: int = 3,
        upload=False,
        pages_per_chunk: int = 3,
    ):
        """
        Main entry point for image-based assignment extraction.

        Automatically switches to chunked processing when the content list
        has more images than `pages_per_chunk` (i.e., large multi-page PDFs).
        For small documents it falls back to the original single-call path.
        """
        # Use chunked path when content is a list of images longer than one chunk
        is_large_document = (
            isinstance(content, list)
            and any(item.get("type") == "image_url" for item in content)
            and len([item for item in content if item.get("type") == "image_url"])
            > pages_per_chunk
        )

        if is_large_document:
            image_items = [
                item
                for item in content
                if isinstance(item, dict) and item.get("type") == "image_url"
            ]
            # text_items = [item for item in content if item.get("type") != "image_url"]

            logger.info(
                f"[Chunked Extraction] Large document detected: {len(image_items)} pages. "
                f"Switching to chunked extraction with {pages_per_chunk} pages/chunk."
            )

            last_error = None
            for attempt in range(max_retries):
                try:
                    return self._extract_assignment_chunked(
                        user=user,
                        image_contents=image_items,
                        upload=upload,
                        pages_per_chunk=pages_per_chunk,
                    )
                except Exception as e:
                    last_error = e
                    logger.warning(
                        f"Chunked extraction attempt {attempt + 1} failed: {str(e)}"
                    )
                    if attempt < max_retries - 1:
                        logger.info("Retrying chunked extraction...")

            raise Exception(
                f"All {max_retries} chunked attempts failed. Last error: {last_error}"
            )

        # Original single-call path for small documents
        last_error = None
        for attempt in range(max_retries):
            try:
                return self.extract_assignment_image(user, content, upload=upload)
            except Exception as e:
                last_error = e
                logger.warning(f"Attempt {attempt + 1} failed: {str(e)}")
                if attempt < max_retries - 1:
                    logger.info("Retrying...")

        raise Exception(f"All {max_retries} attempts failed. Last error: {last_error}")

    def extract_answer(self, user, text):
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
        # content = self.__ai_model(system_prompt, user_prompt)

        response = self.execute_graded_task(
            user=user,
            feature="Answer Extraction",
            task_type="extract_answer",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )

        content = response.choices[0].message.content

        try:
            json_data = json.loads(content)
        except json.JSONDecodeError as e:
            logger.error(f"Error decoding JSON: {str(e)}")
            raise Exception(f"Error decoding JSON: {str(e)}") from Exception

        return json_data

    def extract_answer_image(self, user, content, assignment, assignment_model=None):
        system_prompt = ANSWERS_EXTRACTION_PROMPT

        # Get all the student in this assignment course
        # enrolled_student_names = ""
        student_names = []
        if assignment_model and hasattr(assignment_model, "course"):

            # Fetch all active enrollments for the course
            enrollments = StudentCourse.objects.filter(
                course=assignment_model.course, enrollment_status="ENROLLED"
            ).select_related("student")

            student_names = [
                f"{enrollment.student.first_name} {enrollment.student.last_name}"
                for enrollment in enrollments
            ]

        student_roster = (
            "Here is the list of students in this assignment course: Use it to match, "
            "the student information that is retrieve from the assignment \n "
        )
        student_roster += "\n\n".join(student_names)

        # roster = {"role": "user", "content": student_roster}

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": assignment},
            {"role": "user", "content": student_roster},
            {"role": "user", "content": content},
        ]

        try:
            # response = self.__ai_model(system_prompt, user_prompt=content)
            # response = self.__ai_model(messages=messages)

            response = self.execute_graded_task(
                user=user,
                feature="Answer Extraction",
                task_type="extract_answer",
                messages=messages,
                assignment=assignment_model,
            )

            content = response.choices[0].message.content

        except Exception as e:
            raise Exception(f"Error during AI model: {str(e)}") from Exception

        try:
            json_data = json.loads(content)
        except json.JSONDecodeError as e:
            logger.error(f"Error decoding JSON: {str(e)}")
            raise Exception(f"Error decoding JSON: {str(e)}") from Exception
        return json_data

    def extract_answer_with_retry(
        self, user, content, assignment, assignment_model=None, max_retries: int = 3
    ):
        last_error = None

        for attempt in range(max_retries):
            try:
                return self.extract_answer_image(
                    user, content, assignment, assignment_model=assignment_model
                )
            except Exception as e:
                last_error = e
                logger.warning(f"Attempt {attempt + 1} failed: {str(e)}")

                if attempt < max_retries - 1:
                    logger.info("Retrying...")
        raise Exception(f"All {max_retries} attempts failed. Last error: {last_error}")

    @transaction.atomic
    def grade_student_submission(
        self, user, rubric_json, answer_json, assignment_model=None
    ):
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
        # content = self.__ai_model(system_prompt, user_prompt)

        user_prompts = [{"type": "text", "text": user_prompt}]
        system_prompts = [{"type": "text", "text": system_prompt}]

        response = self.execute_graded_task(
            user=user,
            feature="Grading Assignment",
            task_type="grade_assignment",
            system_prompt=system_prompts,
            user_prompt=user_prompts,
            assignment=assignment_model,
        )

        grade = response.choices[0].message.content

        try:
            json_data = json.loads(grade)
        except json.JSONDecodeError as e:
            logger.error(f"Error decoding JSON: {str(e)}")
            raise Exception(f"Error decoding JSON: {str(e)}") from Exception
        return json_data

    def extract_grade_with_retry(
        self,
        user,
        rubric_json,
        answer_json,
        assignment_model=None,
        max_retries: int = 3,
    ):
        last_error = None

        for attempt in range(max_retries):
            try:
                return self.grade_student_submission(
                    user, rubric_json, answer_json, assignment_model=assignment_model
                )
            except Exception as e:
                last_error = e
                logger.warning(f"Attempt {attempt + 1} failed: {str(e)}")

                if attempt < max_retries - 1:
                    logger.info("Retrying...")

        raise Exception(f"All {max_retries} attempts failed. Last error: {last_error}")

    def generate_assignment_from_prompt(self, user, prompt):
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

        # response = self.__ai_model(messages=messages, tool_schemas=tool_schema)

        response = self.execute_graded_task(
            user=user,
            feature="Assignment Generation",
            task_type="generate_assignment",
            messages=messages,
            tool_schemas=tool_schema,
        )

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

                # response_2 = self.__ai_model(
                #     messages=messages, tool_schemas=tool_schema
                # )

                response_2 = self.execute_graded_task(
                    user=user,
                    feature="Generate Assignment",
                    task_type="generate_assignment",
                    messages=messages,
                    tool_schemas=tool_schema,
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
        self, user, prompt, max_retries: int = 3
    ):
        """
        Retry wrapper for generate_assignment_from_prompt
        """

        last_error = None

        for attempt in range(max_retries):
            try:
                return self.generate_assignment_from_prompt(user, prompt)
            except Exception as e:
                last_error = e
                logger.warning(f"Attempt {attempt + 1} failed: {str(e)}")

                if attempt < max_retries - 1:
                    logger.info("Retrying...")

        raise Exception(f"All {max_retries} attempts failed. Last error: {last_error}")

    def formatted_grade(self, user, user_prompt, assignment_model=None):
        system_prompt = GRADE_FORMATTER

        try:
            # response = self.__ai_model(system_prompt, user_prompt)

            user_prompts = [{"type": "text", "text": user_prompt}]
            system_prompts = [{"type": "text", "text": system_prompt}]

            response = self.execute_graded_task(
                user=user,
                feature="Formatted Grade",
                task_type="formatted_grade",
                system_prompt=system_prompts,
                user_prompt=user_prompts,
                assignment=assignment_model,
            )

            content = response.choices[0].message.content

        except Exception as e:
            raise Exception(f"Error during AI model: {str(e)}") from Exception

        if content:

            try:
                json_data = json.loads(content)
            except json.JSONDecodeError as e:
                logger.error(f"Error decoding JSON: {str(e)}")
                raise Exception(f"Error decoding JSON: {str(e)}") from Exception
            return json_data

        else:
            raise ValueError("content cannot be empty")

    def execute_graded_task(
        self,
        user,
        feature,
        task_type,
        system_prompt=None,
        user_prompt=None,
        messages=None,
        tool_schemas=None,
        respond_format=True,
        assignment=None,
    ):
        # I need the assignment to for students who are submitting
        # their assignment to know who the teacher that created
        # the assignment is and charge the teacher

        # Subscription check

        total_prompt = ""
        image_bytes = []
        pdf_bytes = []

        if user_prompt:
            if isinstance(user_prompt, str):
                total_prompt += user_prompt
            else:
                for prompt in user_prompt:
                    if prompt["type"] == "text":
                        total_prompt += prompt["text"]
                    elif prompt["type"] == "image_url":
                        image_bytes.append(prompt.get("bytes"))
            # total_prompt += user_prompt

        if system_prompt:
            if isinstance(system_prompt, str):
                total_prompt += system_prompt
            else:
                for prompt in system_prompt:
                    if prompt["type"] == "text":
                        total_prompt += prompt["text"]
                    elif prompt["type"] == "image_url":
                        image_bytes.append(prompt.get("bytes"))

        if messages:
            for message in messages:
                content = message["content"]
                if isinstance(content, str):
                    total_prompt += content
                else:
                    for item in content:
                        if item["type"] == "text":
                            total_prompt += item["text"]
                        elif item["type"] == "image_url":
                            image_bytes.append(item.get("bytes"))
                        elif item["type"] == "pdf_url":
                            pdf_bytes.append(item.get("bytes"))

        estimated_cost = self.estimate_total_token(total_prompt, image_bytes, pdf_bytes)

        if user.user_type == UserTypes.STUDENT:
            # Get the TEACHER wallet

            if assignment:
                target_teacher = assignment.course.teacher
                wallet = target_teacher.credit_wallet
            else:
                raise ValueError("Assignment is required for students")
        elif (
            user.user_type == UserTypes.TEACHER
            or user.user_type == UserTypes.SCHOOL_ADMIN
        ):
            target_teacher = user
            wallet = user.credit_wallet
        elif user.user_type == UserTypes.SUPER_ADMIN:
            response = self.__ai_model(
                system_prompt, user_prompt, messages, tool_schemas, respond_format
            )
            return response

        balance = wallet.total_remaining_credits()

        if balance < estimated_cost:
            raise InsufficientCreditsError(
                f"Task requires ~{estimated_cost} credits, but you only have {balance} credits. "
                f"Please refill your wallet to continue"
            )

        if wallet.total_remaining_credits() <= 0:
            raise InsufficientCreditsError("Refill your wallet to continue")

        task_id = str(uuid.uuid4())
        response = self.__ai_model(
            system_prompt, user_prompt, messages, tool_schemas, respond_format
        )

        with transaction.atomic():
            actual_cost = response.usage.total_tokens
            wallet.consume_credits(
                amount=actual_cost,
                feature=feature,
                task_type=task_type,
                task_id=task_id,
            )

            # Update the Beta Analytics Profile for the Teacher
            # This records: raw total, feature mix, and first AI action
            AnalyticsService.record_consumption(
                user=target_teacher, amount=actual_cost, feature=feature
            )

            # Mark the teacher as 'Active' today for the "Active in last 7 days" KPI
            AnalyticsService.track_activity(user=target_teacher)

        return response

    def estimate_image_token_usage(self, width, height):
        """
        Estimate token usage for an image based on its dimensions.
        Using High-Res Token formula
        """

        if width > 2048 or height > 2048:
            ratio = 2048 / max(width, height)
            width, height = width * ratio, height * ratio

        ratio = 768 / min(width, height)
        width, height = width * ratio, height * ratio

        tiles_wide = math.ceil(width / 512)
        tiles_high = math.ceil(height / 512)

        return (tiles_wide * tiles_high * 170) + 85

    def estimate_total_token(self, prompt_text, image_bytes=None, pdf_bytes=None):

        encoding = tiktoken.get_encoding("cl100k_base")

        total_estimate = len(encoding.encode(prompt_text))

        if image_bytes:
            for bytes in image_bytes:
                w, h = ocr_service.get_image_dimensions(bytes)
                total_estimate += self.estimate_image_token_usage(w, h)

        if pdf_bytes:
            for bytes in pdf_bytes:
                pages = pdf_service.get_pdf_page_count(bytes)
                total_estimate += pages * 1200

        total_estimate += 20000

        return total_estimate

    def custom_ai_prompt(
        self, user, user_prompt, role, chat_history=None, feature=None, task_type=None
    ):
        if role == UserTypes.SUPER_ADMIN:
            system_prompt_file = "ai_processor/SUPERADMIN_CUSTOM_PROMPT_2.txt"
        elif role == UserTypes.SCHOOL_ADMIN:
            system_prompt_file = "ai_processor/SCHOOLADMIN_CUSTOM_PROMPT.txt"
        elif role == UserTypes.TEACHER:
            system_prompt_file = "ai_processor/TEACHER_CUSTOM_PROMPT.txt"
        elif role == UserTypes.STUDENT:
            system_prompt_file = "ai_processor/STUDENT_CUSTOM_PROMPT.txt"
        else:
            raise ValueError(f"Invalid role: {role}")

        with open(system_prompt_file, "r") as file:
            system_prompt = file.read()

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        # messages.extend(chat_history)

        try:
            response = self.execute_graded_task(
                user=user,
                feature=feature,
                task_type=task_type,
                messages=messages,
                respond_format=False,
            )

            content = response.choices[0].message.content
        except Exception as e:
            raise Exception(f"Error during AI model: {str(e)}") from Exception

        if content:
            return content
        else:
            raise ValueError("content cannot be empty")

    def custom_ai_prompt_retry(
        self,
        user,
        user_prompt,
        role,
        chat_history=None,
        feature=None,
        task_type=None,
        max_retries: int = 3,
    ):
        last_error = None

        for attempt in range(max_retries):
            try:
                return self.custom_ai_prompt(
                    user,
                    user_prompt,
                    role,
                    chat_history,
                    feature=feature,
                    task_type=task_type,
                )
            except Exception as e:
                last_error = e
                logger.warning(f"Attempt {attempt + 1} failed: {str(e)}")

                if attempt < max_retries - 1:
                    logger.info("Retrying...")

        raise Exception(f"All {max_retries} attempts failed. Last error: {last_error}")

    def generate_student_summary(self, teacher, student, course):
        """
        Generates a short, personalised AI narrative summarising a student's
        performance across all assignments in a given course.

        The resulting text is intended to be stored on StudentCourse.ai_summary
        and displayed to the teacher on the student detail view.

        Args:
            teacher: The CustomUser requesting the summary (credits are charged here)
            student: The CustomUser whose performance is being summarised
            course: The Course object providing the scope

        Returns:
            str: A 3–5 sentence plain-text summary paragraph
        """
        from assignments.models import Assignment
        from classrooms.models import StudentCourse
        from students.models import StudentSubmission

        # --- Gather enrollment info ---
        enrollment = StudentCourse.objects.filter(
            student=student, course=course
        ).first()

        enrollment_status = enrollment.enrollment_status if enrollment else "UNKNOWN"

        # --- Gather all assignments in this course ---
        assignments = Assignment.objects.filter(course=course).order_by("created_at")
        total_assignments = assignments.count()

        # --- Gather student submissions ---
        submissions = StudentSubmission.objects.filter(
            student=student,
            assignment__course=course,
        ).select_related("assignment")

        submission_map = {sub.assignment_id: sub for sub in submissions}
        total_submitted = len(submission_map)

        # --- Build per-assignment breakdown ---
        assignment_details = []
        scores = []

        for assignment in assignments:
            submission = submission_map.get(assignment.id)

            if submission:
                score_pct = (
                    float(submission.score_percentage)
                    if submission.score_percentage is not None
                    else None
                )
                if score_pct is not None:
                    scores.append(score_pct)

                assignment_details.append(
                    f"- {assignment.title!r}: SUBMITTED | "
                    f"Score: {submission.score}/{assignment.total_points} "
                    f"({f'{score_pct:.1f}%' if score_pct is not None else 'ungraded'}) | "
                    f"Grading Confidence: {submission.grading_confidence}% | "
                    f"{'Regraded by teacher' if submission.was_regraded else 'Not regraded'}"
                )
            else:
                assignment_details.append(f"- {assignment.title!r}: NOT SUBMITTED")

        avg_score = round(sum(scores) / len(scores), 1) if scores else None
        submission_rate = (
            round((total_submitted / total_assignments) * 100)
            if total_assignments
            else 0
        )

        # --- Build the structured data payload for the AI ---
        user_prompt = f"""
## Student Information
- Name: {student.get_full_name()}
- Enrollment Status: {enrollment_status}
- Course: {course.name}

## Performance Summary
- Total Assignments in Course: {total_assignments}
- Assignments Submitted: {total_submitted} ({submission_rate}% submission rate)
- Average Score (graded submissions): {f"{avg_score}%" if avg_score is not None else "No graded submissions yet"}

## Assignment Breakdown
{chr(10).join(assignment_details) if assignment_details else "No assignments have been created for this course yet."}

Based on the data above, write a short personalised summary for the teacher."""

        messages = [
            {"role": "system", "content": STUDENT_SUMMARY_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        response = self.execute_graded_task(
            user=teacher,
            feature="Student Summary",
            task_type="student_summary",
            messages=messages,
            respond_format=False,
        )

        content = response.choices[0].message.content

        if not content:
            raise ValueError("AI returned an empty student summary.")

        return content.strip()


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
                # text = ocr_service.extract_with_paddle(image)
                # full_text += text

            self.extracted_data["questions"] = full_text
        except Exception as e:
            raise ValueError(f"Something went wrong: {e}") from Exception

    def get_pdf_page_count(self, pdf_bytes):
        """
        Extracts page count from PDF bytes in-memory.
        """

        try:
            with fitz.open(stream=pdf_bytes, filetype="pdf") as pdf:
                return pdf.page_count
        except Exception as e:
            print(f"PDF page count extraction failed: {e}")
            return 2


class OCRService:

    def get_image_dimensions(self, image_bytes):
        """
        Extracts width and height from image bytes without saving to disk
        """

        try:
            image_bytes = base64.b64decode(image_bytes)
            with Image.open(BytesIO(image_bytes)) as img:
                return img.size
        except Exception as e:
            print(f"Image dimension extraction failed: {e}")
            return (1920, 1000)

    # def __init__(self):
    #     if OCRService._paddle_ocr_model is None:
    #         from paddleocr import PaddleOCR

    #         OCRService._paddle_ocr_model = PaddleOCR(
    #             use_doc_orientation_classify=True,
    #             use_doc_unwarping=True,
    #             use_textline_orientation=True,
    #         )

    # def extract_with_paddle(self, image):
    #     model = OCRService._paddle_ocr_model
    #     img_np = np.array(image.convert("RGB"))
    #     result = model.predict(img_np)

    #     text = ""
    #     for res in result:
    #         text = res.json["res"]["rec_texts"]
    #     return "\n".join(text)

    # def extract_with_pytessaract(self, image):
    #     """

    #     :param image: PIL Image
    #     :return:
    #     """
    #     text = pytesseract.image_to_string(image)
    #     return text


_ocr_instance = None
_pdf_instance = None
_ai_processor_instance = None


ocr_service = OCRService()
pdf_service = PDFService()
ai_processor = AIProcessor()
