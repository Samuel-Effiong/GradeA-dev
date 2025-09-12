"""
Score processor implementation
"""

from typing import Any, Dict


class ScoreProcessor:
    def __init__(self):
        self.similarity_threshold = 0.8  # For fuzzy matching answers

    def calculate_score(
        self,
        assignment_data: Dict[str, Any],
        student_answers: Dict[str, Any],
        rubric_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Calculate score for a student's answers based on the rubric
        """
        total_score = 0
        max_score = rubric_data["total_points"]
        question_scores = {}
        feedback = {}

        # Match answers to rubric items
        for question, details in rubric_data["rubric_items"].items():
            points = details["points"]
            criteria = details["criteria"]

            # Find corresponding student answer
            student_answer = self._find_matching_answer(
                question, student_answers["answers"]
            )

            # Score the answer
            if student_answer:
                score = self._score_answer(student_answer, criteria, points)
                question_scores[question] = score
                total_score += score

                # Generate feedback
                feedback[question] = self._generate_feedback(
                    student_answer, criteria, score, points
                )
            else:
                question_scores[question] = 0
                feedback[question] = "No answer provided"

        return {
            "total_score": total_score,
            "max_score": max_score,
            "percentage": (total_score / max_score * 100) if max_score > 0 else 0,
            "question_scores": question_scores,
            "feedback": feedback,
        }

    def _find_matching_answer(self, question: str, answers: Dict[str, str]) -> str:
        """Find the student's answer that corresponds to a question"""
        # This is a simplified matching - you might want to use more sophisticated
        # text matching algorithms in production
        question_number = self._extract_question_number(question)
        if question_number:
            answer_key = f"answer_{question_number}"
            return answers.get(answer_key, "")
        return ""

    def _score_answer(self, answer: str, criteria: str, max_points: int) -> float:
        """
        Score an individual answer based on criteria
        This is a simplified scoring method - you might want to use more sophisticated
        algorithms in production
        """
        # Convert everything to lowercase for comparison
        answer = answer.lower()
        criteria = criteria.lower()

        # Split criteria into keywords
        keywords = criteria.split()

        # Count matching keywords
        matches = sum(1 for keyword in keywords if keyword in answer)

        # Calculate score based on keyword matches
        score = (matches / len(keywords)) * max_points
        return round(score, 2)

    def _generate_feedback(
        self, answer: str, criteria: str, score: float, max_points: float
    ) -> str:
        """Generate feedback based on the answer and score"""
        if score == max_points:
            return "Excellent! Full points awarded."
        elif score > (max_points * 0.7):
            return "Good answer, but could be more complete."
        elif score > (max_points * 0.4):
            return "Partial credit. Key concepts missing."
        else:
            return "Answer needs significant improvement."

    def _extract_question_number(self, question: str) -> int:
        """Extract question number from question text"""
        try:
            # Look for number at start of question
            words = question.split()
            if words[0].isdigit():
                return int(words[0])
            elif words[0].endswith(".") and words[0][:-1].isdigit():
                return int(words[0][:-1])
        except (IndexError, ValueError):
            pass
        return 0
