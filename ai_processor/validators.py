import logging
from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, validator

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class QuestionType(str, Enum):
    OBJECTIVE = "objective"
    ESSAY = "essay"
    SHORT_ANSWER = "short_answer"


class AssignmentType(str, Enum):
    MIXED = "mixed"
    OBJECTIVE = "objective"
    ESSAY = "essay"
    SHORT_ANSWER = "short_answer"


class ConfidenceLevel(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Question(BaseModel):
    """Individual queston model with validation"""

    question_number: str = Field(
        ..., description="Question identifier (e.g., '1', '2a', 'Question 1')"
    )
    question_text: str = Field(
        ..., min_length=1, description="The complete question text"
    )
    question_type: QuestionType = Field(..., description="Type of question")
    points: float = Field(..., ge=0, description="Point value for this question")
    options: Optional[List[str]] = Field(
        default=None, description="Answer choices for objective questions"
    )
    additional_notes: Optional[str] = Field(
        default=None, description="Special requirements or notes"
    )

    @validator("options")
    def validate_options(cls, v, values):
        """Ensure options are provided only for objective questions"""
        if values.get("queston_type") == QuestionType.OBJECTIVE:
            if not v or len(v) < 2:
                logger.warning("Objective questions must have at multiple options")
        elif values.get("question_type") in [
            QuestionType.ESSAY,
            QuestionType.SHORT_ANSWER,
        ]:
            if v is not None:
                logger.warning(f"Non-objective questions should not have options")

        return v


class AssignmentStructure(BaseModel):
    """Main assignment structure with validation"""

    assignment_name: str = Field(
        ..., min_length=1, description="Title/ name of the assignment"
    )
    instruction: Optional[str] = Field(
        default=None, description="General instructions for students"
    )
    total_points: float = Field(..., ge=0, description="Sum of all question points")
    question_count: int = Field(..., ge=1, description="Total number of questions")
    assignment_type: AssignmentType = Field(..., description="Overall assignment type")
    questions: List[Question] = Field(
        ..., min_items=1, description="List of all questions"
    )
    extraction_confidence: ConfidenceLevel = Field(
        ..., description="Confidence in extraction quality"
    )
    potential_issues: List[str] = Field(
        default_factory=list, description="Identified extraction concerns"
    )

    extracted_at: datetime = Field(default_factory=datetime.now)

    @validator("total_points")
    @classmethod
    def validate_total_points(cls, v, values):
        """Ensure total points matches sum of question points"""
        questions = values.get("questions", [])
        if questions:
            calculated_total = sum(q.points for q in questions)
            if (
                abs(v - calculated_total) > 0.01
            ):  # Allow for small floating point differences
                logger.warning(
                    f"Total points ({v}) doesn't match sum of question points ({calculated_total})"
                )
        return v

    @validator("question_count")
    @classmethod
    def validate_question_count(cls, v, values):
        """Ensure question count matches actual number of questions"""
        questions = values.get("questions", [])
        if len(questions) != v:
            logger.warning(
                f"Question count ({v}) doesn't match actual questions ({len(questions)})"
            )
        return v

    @validator("assignment_type")
    @classmethod
    def validate_assignment_type(cls, v, values):
        """Auto-determine assignment type if mixed"""
        questions = values.get("questions", [])
        if questions:
            question_types = {q.question_type for q in questions}
            if len(question_types) > 1:
                return AssignmentType.MIXED
            elif len(question_types) == 1:
                single_type = next(iter(question_types))
                return AssignmentType(single_type.value)
        return v
