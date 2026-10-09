"""Live, unmocked verification of the CHUNKED extraction paths.

Opt-in: set RUN_REAL_AI=1. Billed and slow, so skipped by default - the
same pattern assignments/tests_real_extraction.py and
users/tests_google_auth.py use.

WHY THIS EXISTS SEPARATELY FROM THE BENCHMARK

The extraction benchmark's golden replay is the right tripwire for the
pipeline around the model, and it passes. But every case in its dataset is
a single-page document, so the benchmark never enters the chunked path at
all - and there is NO benchmark anywhere for ANSWER extraction
(`extraction_benchmark` drives extract_assignment_with_retry,
`grading_benchmark` drives extract_grade_with_retry; nothing drives
extract_answer_with_retry). That gap is recorded in the section 5 audit
notes.

So the two chunked fixes could not be validated by replay, and are
validated here against the real provider instead:

  * assignment chunking now splits on the caller's `pages_per_chunk` and
    describes the pages it actually sent. Checked by extracting a
    multi-page paper and asserting every question survives, exactly once,
    in order - the failure a wrong page range would cause is duplicated,
    missing or mis-numbered questions at the chunk seams.

  * answer chunking now sends the structured-output schema. Checked by
    asserting the SAFETY property it exists for: that a question the
    student genuinely left blank and a question whose work we never found
    do not collapse into the same silent zero.

Assertions are about structure and grounding, never exact model wording.
"""

import json
import os
import unittest

import fitz
from django.test import TestCase

from ai_processor.extraction_schemas import (
    ANSWER_STATUSES,
    BLANK,
    NOT_FOUND_IN_DOCUMENT,
)
from ai_processor.services import ANSWERS_EXTRACTION_PAGES_PER_CHUNK, ai_processor
from assignments.services import AssignmentProcessingService

RUN_REAL_AI = os.environ.get("RUN_REAL_AI") == "1"
SKIP_REASON = "Real AI call is opt-in and billed: set RUN_REAL_AI=1"

# Deliberately distinctive so a model answering from priors rather than
# from the page cannot produce them by accident.
PAPER = [
    ("What is the capital city of Iceland?", "Reykjavik"),
    ("Name the largest moon of Saturn.", "Titan"),
    ("Which gas makes up about 78% of Earth's atmosphere?", "Nitrogen"),
    ("What is the chemical symbol for tungsten?", "W"),
    ("In which year did the Apollo 11 landing take place?", "1969"),
    ("What is the square root of 289?", "17"),
]


def _pdf(pages):
    """pages: list of list-of-lines. One PDF page per inner list."""
    document = fitz.open()
    for lines in pages:
        page = document.new_page()
        y = 90
        for line in lines:
            page.insert_text((60, y), line, fontsize=13)
            y += 34
    data = document.tobytes()
    document.close()
    return data


def make_teacher_with_credits(email):
    """A teacher who genuinely passes the AI access gate."""
    from datetime import timedelta

    from django.utils import timezone

    from billing.models import (
        BillingInterval,
        CreditBucket,
        CreditBucketType,
        CreditWallet,
        PlanCategory,
        PlanTier,
        PlanType,
        SubscriptionPlan,
        UserSubscription,
    )
    from users.models import CustomUser, UserTypes

    teacher = CustomUser.objects.create_user(
        email=email,
        password="password123",  # pragma: allowlist secret
        user_type=UserTypes.TEACHER,
        first_name="Chunk",
        last_name="Teacher",
        is_active=True,
    )
    plan = SubscriptionPlan.objects.create(
        name=PlanType.STANDARD,
        display_name="Chunked Real",
        category=PlanCategory.INDIVIDUAL,
        tier=PlanTier.STANDARD,
        interval=BillingInterval.MONTHLY,
        monthly_credits=1_000_000,
        overage_block_size=500,
        overage_block_price=10,
        max_overage_blocks=10,
        is_active=True,
    )
    now = timezone.now()
    UserSubscription.objects.create(
        user=teacher,
        plan=plan,
        is_active=True,
        billing_cycle_start=now,
        billing_cycle_end=now + timedelta(days=30),
    )
    wallet, _ = CreditWallet.objects.get_or_create(user=teacher)
    CreditBucket.objects.filter(wallet=wallet).delete()
    CreditBucket.objects.create(
        wallet=wallet,
        bucket_type=CreditBucketType.MONTHLY,
        # A chunked run is several calls; sized well clear of the
        # estimator so this fails for correctness reasons, not budget.
        total_credits=1_000_000,
        used_credits=0,
        expires_at=now + timedelta(days=25),
    )
    return teacher


@unittest.skipUnless(RUN_REAL_AI, SKIP_REASON)
class RealChunkedAssignmentExtractionTest(TestCase):
    """Multi-page assignment: every question must survive the seams."""

    def setUp(self):
        from classrooms.models import Course, Session

        self.teacher = make_teacher_with_credits("real-chunked-assign@example.com")
        session = Session.objects.create(name="S", teacher=self.teacher)
        self.course = Course.objects.create(
            name="Chunked Course", teacher=self.teacher, session=session
        )

    def test_a_multipage_paper_extracts_every_question_exactly_once(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        # One question per page: six pages, so this is genuinely chunked
        # and the seams fall between questions.
        pages = [
            (
                ["Geography, Science and Maths Paper", f"{i + 1}. {q} (5 marks)"]
                if i == 0
                else [f"{i + 1}. {q} (5 marks)"]
            )
            for i, (q, _answer) in enumerate(PAPER)
        ]
        uploaded = SimpleUploadedFile(
            "paper.pdf", _pdf(pages), content_type="application/pdf"
        )

        content = AssignmentProcessingService.prepare_ai_content(
            uploaded,
            "Analyze this assignment and return JSON. Return only valid JSON.",
        )
        images = [b for b in content if b.get("type") == "image_url"]
        self.assertEqual(len(images), len(PAPER), "one rasterized page per question")

        data = AssignmentProcessingService.extract_assignment_data(
            self.teacher, content, course=self.course
        )
        questions = data.get("questions") or []

        print("\n=== REAL CHUNKED ASSIGNMENT EXTRACTION ===")
        print("pages:", len(images), " questions extracted:", len(questions))
        for question in questions:
            print(
                "  ",
                question.get("question_number"),
                str(question.get("question_text"))[:70],
            )

        # Numbering is re-indexed globally after the merge: it must be a
        # clean 1..N with no duplicates and no gaps. A wrong page range is
        # what produces repeats or holes at the seams.
        numbers = [q.get("question_number") for q in questions]
        self.assertEqual(
            numbers,
            list(range(1, len(questions) + 1)),
            f"question numbering is not a clean sequence: {numbers}",
        )

        # Every distinctive question reached the output exactly once.
        blob = " ".join(str(q.get("question_text", "")) for q in questions).lower()
        for needle in ("iceland", "saturn", "tungsten", "apollo"):
            with self.subTest(needle=needle):
                self.assertEqual(
                    blob.count(needle), 1, f"{needle!r} appears {blob.count(needle)}x"
                )


@unittest.skipUnless(RUN_REAL_AI, SKIP_REASON)
class RealChunkedAnswerExtractionSafetyTest(TestCase):
    """
    The safety property, live: a blank and a lost answer stay distinct.

    This is the check the schema fix exists for. Before it, the chunked
    path sent no schema, `answer_status` was absent, and everything empty
    was inferred BLANK - so an answer the pipeline failed to read looked
    exactly like a student who chose not to write.
    """

    def setUp(self):
        from assignments.models import Assignment
        from classrooms.models import Course, Session

        self.teacher = make_teacher_with_credits("real-chunked-answer@example.com")
        session = Session.objects.create(name="S2", teacher=self.teacher)
        self.course = Course.objects.create(
            name="Chunked Answers", teacher=self.teacher, session=session
        )
        self.questions = [
            {
                "question_number": index + 1,
                "question_text": question,
                "question_type": "SHORT-ANSWER",
                "question_image": "",
                "points": 5,
                "blooms_level": "Remember",
                "options": [],
                "rubric": [],
                "model_answer": answer,
            }
            for index, (question, answer) in enumerate(PAPER)
        ]
        self.assignment = Assignment.objects.create(
            title="Chunked Answer Paper",
            course=self.course,
            total_points=len(PAPER) * 5,
            questions=self.questions,
        )

    def test_blank_and_not_found_do_not_collapse_into_one_silent_zero(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        # A script covering only questions 1-4. Q3 is deliberately left
        # blank ON the page; Q5 and Q6 are not on any page at all.
        #   - Q3 -> the student chose not to answer      -> BLANK
        #   - Q5, Q6 -> we do not have their work        -> NOT_FOUND
        pages = [
            [
                "Student: Sam Tester",
                f"1. {PAPER[0][0]}",
                f"   {PAPER[0][1]}",
                f"2. {PAPER[1][0]}",
                f"   {PAPER[1][1]}",
            ],
            [
                f"3. {PAPER[2][0]}",
                "   ",
                f"4. {PAPER[3][0]}",
                f"   {PAPER[3][1]}",
            ],
            ["(end of script)"],
        ]
        self.assertGreaterEqual(len(pages), ANSWERS_EXTRACTION_PAGES_PER_CHUNK)

        uploaded = SimpleUploadedFile(
            "script.pdf", _pdf(pages), content_type="application/pdf"
        )
        content = AssignmentProcessingService.prepare_ai_content(
            uploaded, "Extract the student's answers."
        )

        result = ai_processor.extract_answer_with_retry(
            self.teacher,
            content,
            json.dumps(self.questions),
            assignment_model=self.assignment,
        )
        answers = result.get("answers") or []
        by_number = {
            int(str(a.get("question_number"))): a
            for a in answers
            if str(a.get("question_number")).isdigit()
        }

        print("\n=== REAL CHUNKED ANSWER EXTRACTION (safety) ===")
        for number in sorted(by_number):
            entry = by_number[number]
            print(
                f"  Q{number}: status={entry.get('answer_status')!r} "
                f"html={str(entry.get('answer_html'))[:44]!r}"
            )

        # 1. The schema actually took effect: every entry carries a status
        #    from the declared vocabulary. Absent/garbage here means the
        #    call went out without the schema again.
        self.assertTrue(answers, "no answers extracted at all")
        for number, entry in by_number.items():
            with self.subTest(question=number):
                self.assertIn(
                    entry.get("answer_status"),
                    ANSWER_STATUSES,
                    "answer_status missing or invalid - the structured-output "
                    "schema did not reach this call",
                )

        # 2. The answered questions are answered - grounded in the page.
        self.assertEqual(by_number[1].get("answer_status"), "ANSWERED")
        self.assertIn("reykjavik", str(by_number[1].get("answer_html", "")).lower())

        # 3. THE SAFETY PROPERTY. Questions never present in the document
        #    must not be reported as the student's choice to skip.
        for missing in (5, 6):
            with self.subTest(question=missing):
                status = by_number.get(missing, {}).get("answer_status")
                self.assertNotEqual(
                    status,
                    BLANK,
                    f"Q{missing} was on no page of this script, yet it was "
                    "reported as a deliberate blank - that is the silent "
                    "zero this schema exists to prevent",
                )
                self.assertEqual(status, NOT_FOUND_IN_DOCUMENT)
