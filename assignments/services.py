from django.core.files.uploadedfile import UploadedFile
from PIL.Image import Image

from ai_processor.services import ai_processor, ocr_service, pdf_service

# from assignments.models import Assignment

image_formats = [
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
]

pdf_formats = "application/pdf"


def process_files(files):
    print("Files: ", files)
    results = []
    print(results)

    for uploaded_file in files:
        # Check if it's an instance of UploadedFile
        if not isinstance(uploaded_file, UploadedFile):
            return ({"error": "Invalid file upload"},)

        if uploaded_file.content_type in image_formats:
            try:
                image = Image.open(uploaded_file)
                questions = ocr_service.extract_with_paddle(image)

                assignment_questions = ai_processor.extract_assignment_with_retry(
                    questions, max_retries=3
                )

                results.append(assignment_questions)

            except Exception as e:
                return {"error": str(e)}

        elif uploaded_file.content_type == pdf_formats:
            try:
                pdf_service.set_uploaded_file(uploaded_file)
                extracted_data = pdf_service.extract()

                assignment_questions = ai_processor.extract_assignment_with_retry(
                    extracted_data["questions"], max_retries=3
                )

                results.append(assignment_questions)

            except Exception as e:
                return {"error": str(e)}

        else:
            return (
                {
                    "error": f"File `{uploaded_file.name}` has an invalid format. Only images "
                    f"(JPEG, PNG, GIF, WebP) and PDFs are allowed."
                },
            )

    return results


def hook_process_files(task):
    result = task.result

    print(result)
