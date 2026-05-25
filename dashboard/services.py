from collections import Counter, defaultdict
from datetime import timedelta

from django.db.models import Prefetch
from django.utils import timezone

from assignments.models import Assignment, AssignmentStatus
from classrooms.models import Course, EnrollmentStatusType, StudentCourse
from students.models import StudentSubmission


class DashboardService:
    def analyze_question_difficulty(self, submissions):
        question_scores = defaultdict(list)

        for submission in submissions:
            if not submission.answers:
                continue

            for q_id, q_data in submission.answers.items():
                score = q_data.get("score")
                if score is not None:
                    question_scores[q_id].append(score)

        if not question_scores:
            return [], []

        question_averages = {
            q: sum(scores) / len(scores) for q, scores in question_scores.items()
        }

        hardest = sorted(question_averages.items(), key=lambda x: x[1])[:2]
        easiest = sorted(question_averages.items(), key=lambda x: x[1], reverse=True)[
            :2
        ]

        return hardest, easiest

    def get_ai_context(self, user):
        pass


class WeeklyCourseSummaryService:
    WINDOW_DAYS = 7
    TREND_SCORE_WINDOW = 3
    GRADE_RISK_THRESHOLD = 70.0
    GRADE_WARNING_THRESHOLD = 75.0
    SUBMISSION_RISK_THRESHOLD = 0.70
    CRITICAL_SUBMISSION_THRESHOLD = 0.50
    GRADE_TREND_DELTA = 3.0

    def build_course_summary(self, course, *, as_of=None, window_days=None):
        course = self._resolve_course(course)
        as_of = as_of or timezone.now()
        window_days = window_days or self.WINDOW_DAYS
        recent_start = as_of - timedelta(days=window_days)
        previous_start = recent_start - timedelta(days=window_days)

        published_assignments = list(
            Assignment.objects.filter(course=course, status=AssignmentStatus.PUBLISHED)
            .select_related("topic")
            .order_by("created_at", "title")
        )

        relevant_assignments = [
            assignment
            for assignment in published_assignments
            if assignment.due_date is None or assignment.due_date <= as_of
        ]
        if not relevant_assignments:
            relevant_assignments = published_assignments

        relevant_assignment_ids = {assignment.id for assignment in relevant_assignments}

        course_submissions_qs = (
            StudentSubmission.objects.filter(assignment__course=course)
            .select_related("assignment", "assignment__topic")
            .order_by("submission_date", "graded_at")
        )

        enrollments = list(
            StudentCourse.objects.filter(
                course=course,
                enrollment_status=EnrollmentStatusType.ENROLLED,
            )
            .select_related("student", "course")
            .prefetch_related(
                Prefetch(
                    "student__submissions",
                    queryset=course_submissions_qs,
                    to_attr="course_submissions",
                )
            )
        )

        student_records = []
        all_relevant_submissions = []
        all_relevant_graded_scores = []

        for enrollment in enrollments:
            student = enrollment.student
            student_submissions = list(getattr(student, "course_submissions", []))
            relevant_submissions = [
                submission
                for submission in student_submissions
                if submission.assignment_id in relevant_assignment_ids
            ]
            all_relevant_submissions.extend(relevant_submissions)

            student_record = self._build_student_record(
                enrollment=enrollment,
                relevant_submissions=relevant_submissions,
                expected_assignment_count=len(relevant_assignments),
            )
            student_records.append(student_record)
            all_relevant_graded_scores.extend(student_record["graded_scores"])

        student_records.sort(
            key=lambda item: (
                not item["at_risk"],
                item["student_name"].lower(),
            )
        )

        overall_metrics = self._build_overall_metrics(
            course=course,
            published_assignments=published_assignments,
            relevant_assignments=relevant_assignments,
            submissions=all_relevant_submissions,
            graded_scores=all_relevant_graded_scores,
            as_of=as_of,
            recent_start=recent_start,
            previous_start=previous_start,
            student_count=len(enrollments),
        )

        at_risk_students = [
            self._serialize_student_for_output(student)
            for student in student_records
            if student["at_risk"]
        ]
        commonalities = self._build_commonalities(student_records)
        trending = self._build_trend_watch(student_records)
        interventions = self._build_interventions(
            course=course,
            student_records=student_records,
            commonalities=commonalities,
        )

        return {
            "course": {
                "id": course.id,
                "name": course.name,
                "session_name": course.session.name if course.session else None,
                "teacher_id": course.teacher_id,
                "generated_at": as_of,
                "window_days": window_days,
            },
            "overall": overall_metrics,
            "overall_summary": self._build_overall_summary(course, overall_metrics),
            "at_risk_students": at_risk_students,
            "commonalities": commonalities,
            "trend_watch": trending,
            "interventions": interventions,
        }

    def _resolve_course(self, course):
        if isinstance(course, Course):
            return course

        return Course.objects.select_related("teacher", "session").get(id=course)

    def _build_student_record(
        self,
        *,
        enrollment,
        relevant_submissions,
        expected_assignment_count,
    ):
        graded_submissions = self._sorted_graded_submissions(relevant_submissions)
        graded_scores = [
            float(submission.score_percentage)
            for submission in graded_submissions
            if submission.score_percentage is not None
        ]

        recent_scores = graded_scores[-self.TREND_SCORE_WINDOW :]
        submitted_assignment_ids = {
            submission.assignment_id for submission in relevant_submissions
        }
        submitted_count = len(submitted_assignment_ids)
        missing_count = max(expected_assignment_count - submitted_count, 0)

        if expected_assignment_count:
            submission_rate = submitted_count / expected_assignment_count
        else:
            submission_rate = 1.0

        average_grade = (
            round(sum(graded_scores) / len(graded_scores), 2) if graded_scores else None
        )
        grade_trend, trend_delta = self._calculate_grade_trend(recent_scores)
        low_performance_topics, low_performance_assignments = (
            self._collect_low_performance_patterns(graded_submissions)
        )

        at_risk, issue_tags, risk_reasons = self._evaluate_student_risk(
            expected_assignment_count=expected_assignment_count,
            submitted_count=submitted_count,
            submission_rate=submission_rate,
            average_grade=average_grade,
            grade_trend=grade_trend,
        )

        return {
            "student_id": enrollment.student.id,
            "student_name": enrollment.student.get_full_name(),
            "ai_student_summary": enrollment.ai_summary,
            "average_grade": average_grade,
            "assignments_submitted": submitted_count,
            "assignments_expected": expected_assignment_count,
            "missing_assignments_count": missing_count,
            "submission_rate": round(submission_rate * 100, 2),
            "grade_trend": grade_trend,
            "trend_delta": trend_delta,
            "recent_scores": recent_scores,
            "graded_scores": graded_scores,
            "issue_tags": issue_tags,
            "risk_reasons": risk_reasons,
            "at_risk": at_risk,
            "low_performance_topics": low_performance_topics,
            "low_performance_assignments": low_performance_assignments,
        }

    def _evaluate_student_risk(
        self,
        *,
        expected_assignment_count,
        submitted_count,
        submission_rate,
        average_grade,
        grade_trend,
    ):
        issue_tags = []
        reasons = []

        if expected_assignment_count and submitted_count == 0:
            issue_tags.append("missing_submissions")
            reasons.append("No submitted work for course assignments due so far.")
        elif (
            expected_assignment_count
            and submission_rate < self.SUBMISSION_RISK_THRESHOLD
        ):
            issue_tags.append("missing_submissions")
            missing_count = expected_assignment_count - submitted_count
            reasons.append(
                f"Submission rate is {round(submission_rate * 100, 1)}% with "
                f"{missing_count} missing assignment(s)."
            )

        if average_grade is not None and average_grade < self.GRADE_RISK_THRESHOLD:
            if submission_rate >= self.SUBMISSION_RISK_THRESHOLD:
                issue_tags.append("conceptual_gaps")
                reasons.append(
                    f"Average graded score is {average_grade}%, which suggests conceptual gaps despite "
                    "regular submission."
                )
            else:
                issue_tags.append("low_scores")
                reasons.append(
                    f"Average graded score is {average_grade}%, below the {self.GRADE_RISK_THRESHOLD:.0f}% target."
                )

        if grade_trend == "DECLINING":
            issue_tags.append("declining_performance")
            reasons.append("Recent graded work is trending downward.")

        moderate_flags = 0
        if (
            expected_assignment_count
            and submission_rate < self.SUBMISSION_RISK_THRESHOLD
        ):
            moderate_flags += 1
        if average_grade is not None and average_grade < self.GRADE_WARNING_THRESHOLD:
            moderate_flags += 1
        if grade_trend == "DECLINING":
            moderate_flags += 1

        critical_low_grade = (
            average_grade is not None and average_grade < self.GRADE_RISK_THRESHOLD
        )
        critical_missing = (
            expected_assignment_count >= 2
            and submission_rate < self.CRITICAL_SUBMISSION_THRESHOLD
        )

        at_risk = critical_low_grade or critical_missing or moderate_flags >= 2
        return at_risk, issue_tags, reasons

    def _collect_low_performance_patterns(self, graded_submissions):
        topic_names = set()
        assignment_titles = set()

        for submission in graded_submissions:
            if submission.score_percentage is None:
                continue
            if float(submission.score_percentage) >= self.GRADE_RISK_THRESHOLD:
                continue

            if submission.assignment.topic_id and submission.assignment.topic:
                topic_names.add(submission.assignment.topic.name)
            elif submission.assignment.title:
                assignment_titles.add(submission.assignment.title)

        return sorted(topic_names), sorted(assignment_titles)

    def _build_overall_metrics(
        self,
        *,
        course,
        published_assignments,
        relevant_assignments,
        submissions,
        graded_scores,
        as_of,
        recent_start,
        previous_start,
        student_count,
    ):
        expected_submissions = len(relevant_assignments) * student_count
        unique_submissions = {
            submission.id: submission for submission in submissions
        }.values()
        submitted_count = len(unique_submissions)
        submission_rate = (
            round((submitted_count / expected_submissions) * 100, 2)
            if expected_submissions
            else 100.0
        )
        average_grade = (
            round(sum(graded_scores) / len(graded_scores), 2) if graded_scores else None
        )

        recent_submissions = [
            submission
            for submission in unique_submissions
            if recent_start <= submission.submission_date <= as_of
        ]
        previous_submissions = [
            submission
            for submission in unique_submissions
            if previous_start <= submission.submission_date < recent_start
        ]

        recent_graded_scores = [
            float(submission.score_percentage)
            for submission in unique_submissions
            if submission.score_percentage is not None
            and submission.graded_at
            and recent_start <= submission.graded_at <= as_of
        ]
        previous_graded_scores = [
            float(submission.score_percentage)
            for submission in unique_submissions
            if submission.score_percentage is not None
            and submission.graded_at
            and previous_start <= submission.graded_at < recent_start
        ]

        recent_average_grade = (
            round(sum(recent_graded_scores) / len(recent_graded_scores), 2)
            if recent_graded_scores
            else None
        )
        previous_average_grade = (
            round(sum(previous_graded_scores) / len(previous_graded_scores), 2)
            if previous_graded_scores
            else None
        )

        grade_trend = self._trend_from_values(
            previous_average_grade,
            recent_average_grade,
            threshold=self.GRADE_TREND_DELTA,
        )
        submission_trend = self._trend_from_values(
            len(previous_submissions),
            len(recent_submissions),
            threshold=1,
        )

        return {
            "student_count": student_count,
            "published_assignment_count": len(published_assignments),
            "relevant_assignment_count": len(relevant_assignments),
            "submission_count": submitted_count,
            "expected_submission_count": expected_submissions,
            "submission_rate": submission_rate,
            "graded_submission_count": len(graded_scores),
            "average_grade": average_grade,
            "recent_submission_count": len(recent_submissions),
            "previous_submission_count": len(previous_submissions),
            "submission_trend": submission_trend,
            "recent_average_grade": recent_average_grade,
            "previous_average_grade": previous_average_grade,
            "grade_trend": grade_trend,
            "term_label": (
                course.session.name if course.session else "Current course term"
            ),
        }

    def _build_overall_summary(self, course, metrics):
        avg_grade = (
            f"{metrics['average_grade']}%"
            if metrics["average_grade"] is not None
            else "no graded work yet"
        )
        recent_grade = (
            f"{metrics['recent_average_grade']}%"
            if metrics["recent_average_grade"] is not None
            else "no recent graded work"
        )
        previous_grade = (
            f"{metrics['previous_average_grade']}%"
            if metrics["previous_average_grade"] is not None
            else "no prior graded work"
        )

        return (
            f"{course.name} currently has {metrics['student_count']} enrolled student(s) and "
            f"{metrics['published_assignment_count']} published assignment(s) so far in "
            f"{metrics['term_label']}. Students have completed "
            f"{metrics['submission_count']} of {metrics['expected_submission_count']} expected "
            f"submission(s) ({metrics['submission_rate']}%), and the average graded score across "
            f"the course is {avg_grade}. Over the last week, submission activity is "
            f"{metrics['submission_trend'].lower()} compared with the prior week, while recent "
            f"graded performance is {metrics['grade_trend'].lower()} "
            f"({recent_grade} versus {previous_grade})."
        )

    def _build_commonalities(self, student_records):
        at_risk_students = [
            student for student in student_records if student["at_risk"]
        ]
        if not at_risk_students:
            return []

        issue_counter = Counter(
            tag for student in at_risk_students for tag in student["issue_tags"]
        )
        commonalities = []

        if issue_counter["missing_submissions"]:
            commonalities.append(
                f"{issue_counter['missing_submissions']} at-risk student(s) are "
                "being pulled down by missing submissions."
            )

        conceptual_count = (
            issue_counter["conceptual_gaps"] + issue_counter["low_scores"]
        )
        if conceptual_count:
            commonalities.append(
                f"{conceptual_count} at-risk student(s) are showing low-score "
                "patterns that suggest comprehension gaps."
            )

        if issue_counter["declining_performance"]:
            commonalities.append(
                f"{issue_counter['declining_performance']} at-risk student(s) are "
                "trending downward on recent graded work."
            )

        topic_counter = Counter()
        assignment_counter = Counter()
        for student in at_risk_students:
            for topic_name in student["low_performance_topics"]:
                topic_counter[topic_name] += 1
            for assignment_title in student["low_performance_assignments"]:
                assignment_counter[assignment_title] += 1

        top_topic, topic_count = self._top_counter_item(topic_counter)
        if top_topic and topic_count >= 2:
            commonalities.append(
                f"Several at-risk students are struggling on work connected "
                f"to the topic {top_topic!r}."
            )
        else:
            top_assignment, assignment_count = self._top_counter_item(
                assignment_counter
            )
            if top_assignment and assignment_count >= 2:
                commonalities.append(
                    f"Several at-risk students are underperforming on {top_assignment!r}."
                )

        return commonalities

    def _build_trend_watch(self, student_records):
        trending_up = sorted(
            [
                student
                for student in student_records
                if student["grade_trend"] == "IMPROVING"
            ],
            key=lambda item: item["trend_delta"],
            reverse=True,
        )[:5]
        trending_down = sorted(
            [
                student
                for student in student_records
                if student["grade_trend"] == "DECLINING" and not student["at_risk"]
            ],
            key=lambda item: item["trend_delta"],
        )[:5]

        return {
            "trending_up": [
                self._serialize_trend_student(student) for student in trending_up
            ],
            "trending_down": [
                self._serialize_trend_student(student) for student in trending_down
            ],
        }

    def _build_interventions(self, *, course, student_records, commonalities):
        interventions = []
        at_risk_students = [
            student for student in student_records if student["at_risk"]
        ]
        issue_counter = Counter(
            tag for student in at_risk_students for tag in student["issue_tags"]
        )

        if issue_counter["missing_submissions"]:
            interventions.append(
                {
                    "scope": "course",
                    "target": course.name,
                    "reason": "Missing submissions are a recurring issue.",
                    "recommendation": (
                        "Set a catch-up checkpoint, contact missing students "
                        "directly, and give a short make-up submission window where appropriate."
                    ),
                }
            )

        conceptual_count = (
            issue_counter["conceptual_gaps"] + issue_counter["low_scores"]
        )
        if conceptual_count:
            interventions.append(
                {
                    "scope": "course",
                    "target": course.name,
                    "reason": "Low scores suggest a shared comprehension issue.",
                    "recommendation": (
                        "Re-teach the weakest concept or assignment area in a focused "
                        "mini-lesson and assign one targeted follow-up practice task."
                    ),
                }
            )

        if issue_counter["declining_performance"]:
            interventions.append(
                {
                    "scope": "course",
                    "target": course.name,
                    "reason": "Some students are slipping on recent work.",
                    "recommendation": (
                        "Schedule quick check-ins with students trending downward "
                        "before the decline becomes persistent."
                    ),
                }
            )

        for student in at_risk_students:
            interventions.append(
                {
                    "scope": "student",
                    "target": student["student_name"],
                    "student_id": student["student_id"],
                    "reason": (
                        student["risk_reasons"][0]
                        if student["risk_reasons"]
                        else "General at-risk pattern detected."
                    ),
                    "recommendation": self._recommend_student_intervention(student),
                }
            )

        if not at_risk_students and commonalities:
            interventions.append(
                {
                    "scope": "course",
                    "target": course.name,
                    "reason": "No students are currently at risk, but trend patterns are worth monitoring.",
                    "recommendation": (
                        "Use the trend watch list to identify students who may need "
                        "lighter-touch support before they move into the at-risk group."
                    ),
                }
            )

        return interventions

    def _recommend_student_intervention(self, student):
        tags = set(student["issue_tags"])
        if "missing_submissions" in tags:
            return "Reach out directly, identify the blocker, and create a short recovery plan for missed work."
        if "conceptual_gaps" in tags or "low_scores" in tags:
            return (
                "Provide re-teaching or targeted practice on the weakest concept area and review the next "
                "submission closely."
            )
        if "declining_performance" in tags:
            return "Schedule a brief check-in to understand what changed and put a short support plan in place."
        return "Monitor the student closely and review the next assignment for signs of continued difficulty."

    def _serialize_student_for_output(self, student):
        return {
            "student_id": student["student_id"],
            "student_name": student["student_name"],
            "average_grade": student["average_grade"],
            "assignments_submitted": student["assignments_submitted"],
            "assignments_expected": student["assignments_expected"],
            "missing_assignments_count": student["missing_assignments_count"],
            "submission_rate": student["submission_rate"],
            "grade_trend": student["grade_trend"],
            "recent_scores": student["recent_scores"],
            "ai_student_summary": student["ai_student_summary"],
            "risk_reasons": student["risk_reasons"],
            "issue_tags": student["issue_tags"],
        }

    def _serialize_trend_student(self, student):
        return {
            "student_id": student["student_id"],
            "student_name": student["student_name"],
            "average_grade": student["average_grade"],
            "submission_rate": student["submission_rate"],
            "grade_trend": student["grade_trend"],
            "trend_delta": student["trend_delta"],
        }

    def _sorted_graded_submissions(self, submissions):
        graded_submissions = [
            submission
            for submission in submissions
            if submission.score_percentage is not None
        ]
        return sorted(
            graded_submissions,
            key=lambda submission: (
                submission.graded_at or submission.submission_date,
                submission.submission_date,
            ),
        )

    def _calculate_grade_trend(self, recent_scores):
        if len(recent_scores) < 2:
            return "INSUFFICIENT_DATA", 0.0

        first_score = recent_scores[0]
        last_score = recent_scores[-1]
        delta = round(last_score - first_score, 2)

        if delta >= self.GRADE_TREND_DELTA:
            return "IMPROVING", delta
        if delta <= -self.GRADE_TREND_DELTA:
            return "DECLINING", delta
        return "STABLE", delta

    def _trend_from_values(self, previous_value, current_value, *, threshold):
        if previous_value is None and current_value is None:
            return "INSUFFICIENT_DATA"
        if previous_value is None:
            return "IMPROVING" if current_value else "INSUFFICIENT_DATA"
        if current_value is None:
            return "DECLINING"

        delta = current_value - previous_value
        if delta >= threshold:
            return "IMPROVING"
        if delta <= -threshold:
            return "DECLINING"
        return "STABLE"

    def _top_counter_item(self, counter):
        if not counter:
            return None, 0
        return counter.most_common(1)[0]
