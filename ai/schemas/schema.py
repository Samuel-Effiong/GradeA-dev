"""
JSON schemas for data validation
"""

from typing import Dict, List, Optional, TypedDict, Union


class StudentInfo(TypedDict):
    name: str
    section: str


class Metadata(TypedDict):
    source: str
    page_count: Optional[int]


class AssignmentSchema(TypedDict):
    type: str  # Must be "assignment"
    instructions: str
    questions: List[str]
    metadata: Metadata


class AnswerSchema(TypedDict):
    type: str  # Must be "answer"
    student_info: StudentInfo
    answers: Dict[str, str]  # question_number -> answer
    metadata: Metadata


class RubricItem(TypedDict):
    points: int
    criteria: str


class RubricSchema(TypedDict):
    type: str  # Must be "rubric"
    rubric_items: Dict[str, RubricItem]  # question -> {points, criteria}
    total_points: int
    metadata: Metadata


class ScoreSchema(TypedDict):
    total_score: float
    max_score: float
    percentage: float
    question_scores: Dict[str, float]
    feedback: Dict[str, str]
