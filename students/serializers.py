from django.utils import timezone
from rest_framework import serializers

from users.models import CustomUser

from .models import StudentSubmission
from .services import get_grade_details


class StudentSerializer(serializers.ModelSerializer):
    enrollment_status = serializers.SerializerMethodField(
        method_name="get_enrollment_status"
    )
    is_system_generated_email = serializers.SerializerMethodField()

    class Meta:
        model = CustomUser
        fields = [
            "id",
            "first_name",
            "last_name",
            "email",
            "is_active",
            "profile_image",
            "enrollment_status",
            "is_system_generated_email",
        ]

    def get_is_system_generated_email(self, obj) -> bool:
        return bool(obj.email and str(obj.email).endswith("@student.local"))

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if data.get("is_system_generated_email"):
            data["email"] = None
        return data

    def get_enrollment_status(self, obj) -> str | None:
        """Returns the enrollment status for this student in the course provided via serializer context.
        If no course is provided or a student is not enrolled in that course, returns None.
        """
        course = self.context.get("course")

        if not course:
            return None

        enrollment = obj.enrollments.filter(course=course).first()
        return enrollment.enrollment_status if enrollment else None


class StudentSubmissionSerializer(serializers.ModelSerializer):
    student_name = serializers.SerializerMethodField()
    assignment_title = serializers.CharField(source="assignment.title", read_only=True)

    class Meta:
        model = StudentSubmission
        fields = [
            "id",
            "student",
            "student_name",
            "assignment",
            "assignment_title",
            "answers",
            "score",
            "max_points",
            "feedback",
            "submission_date",
            "graded_at",
            "grading_confidence",
            "raw_input",
            "formatted_grade",
        ]

        read_only_fields = [
            "score",
            "feedback",
            "submission_date",
            "student_name",
            "assignment_title",
            "grade_at",
            "grading_confidence",
            "raw_input",
            "formatted_grade",
        ]

    def get_student_name(self, obj) -> str:
        return f"{obj.student.first_name} {obj.student.last_name}"

    def update(self, instance, validated_data):
        # request = self.context.get("request")
        # user = request.user if request else None

        # ONly track regardes AFTER AI has graded
        if instance.ai_graded_at:
            score_changed = (
                "score" in validated_data
                and validated_data["score"] != instance.ai_score
            )

            feedback_changed = (
                "feedback" in validated_data
                and validated_data["feedback"] != instance.ai_feedback
            )

            if score_changed or feedback_changed:
                instance.was_regraded = True
                instance.regraded_at = timezone.now()

        return super().update(instance, validated_data)


class StudentSubmissionUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = StudentSubmission
        fields = [
            "id",
            "raw_input",
        ]

        read_only_fields = [
            "id",
        ]


class StudentSubmissionListSerializer(serializers.ModelSerializer):
    student_name = serializers.SerializerMethodField()
    assignment_title = serializers.CharField(source="assignment.title", read_only=True)
    course = serializers.CharField(source="assignment.course.id", read_only=True)
    score = serializers.SerializerMethodField()
    score_percentage = serializers.SerializerMethodField()
    is_grading_scheduled = serializers.SerializerMethodField()
    max_points = serializers.IntegerField(
        source="assignment.total_points", read_only=True
    )

    class Meta:
        model = StudentSubmission
        fields = [
            "id",
            "student",
            "student_name",
            "assignment",
            "assignment_title",
            "course",
            "submission_date",
            "score",
            "score_percentage",
            "max_points",
            "graded_at",
            "is_published",
            "grading_confidence",
            "scheduled_grading_at",
            "grading_task_name",
            "is_grading_scheduled",
        ]

        read_only_fields = [
            "submission_date",
            "student_name",
            "assignment_title",
            "course",
            "score",
            "score_percentage",
            "max_points",
            "graded_at",
            "is_published",
            "grading_confidence",
            "scheduled_grading_at",
            "grading_task_name",
            "is_grading_scheduled",
        ]

    def get_student_name(self, obj) -> str:
        return f"{obj.student.first_name} {obj.student.last_name}"

    def get_score(self, obj):
        request = self.context.get("request")
        if request and request.user.user_type == "STUDENT" and not obj.is_published:
            return None
        return obj.score

    def get_score_percentage(self, obj):
        request = self.context.get("request")
        if request and request.user.user_type == "STUDENT" and not obj.is_published:
            return None
        return obj.score_percentage

    def get_is_grading_scheduled(self, obj) -> bool:
        return bool(
            obj.scheduled_grading_at and obj.scheduled_grading_at > timezone.now()
        )


class StudentSubmissionDetailSerializer(serializers.ModelSerializer):
    score = serializers.SerializerMethodField()
    score_percentage = serializers.SerializerMethodField()
    # feedback = serializers.SerializerMethodField()
    formatted_grade = serializers.SerializerMethodField()
    full_name = serializers.CharField(source="student.get_full_name", read_only=True)
    first_name = serializers.CharField(source="student.first_name", read_only=True)
    last_name = serializers.CharField(source="student.last_name", read_only=True)
    email = serializers.EmailField(source="student.email", read_only=True)
    is_grading_scheduled = serializers.SerializerMethodField()
    submission_status = serializers.SerializerMethodField()
    email = serializers.SerializerMethodField()
    max_points = serializers.IntegerField(
        source="assignment.total_points", read_only=True
    )

    grade_status = serializers.SerializerMethodField()

    class Meta:
        model = StudentSubmission
        fields = [
            "id",
            "assignment",
            "student",
            "full_name",
            "first_name",
            "last_name",
            "email",
            "submission_status",
            "score",
            "max_points",
            "score_percentage",
            "was_regraded",
            "regraded_at",
            "grade_status",
            "is_published",
            "submission_date",
            "raw_input",
            "formatted_grade",
            "answers",
            "scheduled_grading_at",
            "grading_task_name",
            "is_grading_scheduled",
        ]

        read_only_fields = [
            "assignment",
            "student",
            "full_name",
            "email",
            "submission_status",
            "grade_status",
            "submission_date",
            "raw_input",
            "score",
            "max_points",
            "score_percentage",
            "was_regraded",
            "regraded_at",
            "formatted_grade",
            "is_published",
            "answers",
            "scheduled_grading_at",
            "grading_task_name",
            "is_grading_scheduled",
        ]

    def get_email(self, obj):
        if "student.local" in obj.student.email:
            return None
        return obj.student.email

    def get_score(self, obj):
        request = self.context.get("request")
        if request and request.user.user_type == "STUDENT" and not obj.is_published:
            return None
        return obj.score

    def get_score_percentage(self, obj):
        request = self.context.get("request")
        if request and request.user.user_type == "STUDENT" and not obj.is_published:
            return None
        return obj.score_percentage

    # def get_feedback(self, obj):
    #     request = self.context.get("request")
    #     if (
    #         request
    #         and request.user.user_type == "STUDENT"
    #         and not obj.is_published
    #     ):
    #         return None
    #     return obj.feedback

    def get_formatted_grade(self, obj):
        request = self.context.get("request")
        if request and request.user.user_type == "STUDENT" and not obj.is_published:
            return None
        return obj.formatted_grade

    def get_submission_status(self, obj):
        return "SUBMITTED"

    def get_grade_status(self, obj):
        if obj.graded_at and obj.is_published:
            return "GRADED"
        return "NOT GRADED"

    def get_is_grading_scheduled(self, obj) -> bool:
        return bool(
            obj.scheduled_grading_at and obj.scheduled_grading_at > timezone.now()
        )


class StudentSubmissionDetailStudentVersionSerializer(serializers.ModelSerializer):
    score = serializers.SerializerMethodField()
    score_percentage = serializers.SerializerMethodField()
    formatted_grade = serializers.SerializerMethodField()
    submission_status = serializers.SerializerMethodField()
    max_points = serializers.IntegerField(
        source="assignment.total_points", read_only=True
    )
    grade_status = serializers.SerializerMethodField()

    assignment_title = serializers.CharField(source="assignment__title", read_only=True)
    assignment_due_date = serializers.CharField(
        source="assignment__due_date", read_only=True
    )
    # assignment_status = serializers.SerializerMethodField()

    course_title = serializers.CharField(
        source="assignment__course__title", read_only=True
    )

    grade_letter = serializers.SerializerMethodField()
    feedback = serializers.SerializerMethodField()

    class Meta:
        model = StudentSubmission
        fields = [
            "id",
            "assignment",
            "assignment_title",
            "assignment_due_date",
            # "assignment_status",
            "course_title",
            "submission_status",
            "score",
            "max_points",
            "score_percentage",
            "grade_status",
            "submission_date",
            "raw_input",
            "formatted_grade",
            "grade_letter",
            "feedback",
            "is_published",
        ]

        read_only_fields = [
            "assignment",
            "assignment_title",
            "assignment_due_date",
            # "assignment_status",
            "course_title",
            "submission_status",
            "raw_input",
            "score",
            "grade_status",
            "formatted_grade",
            "max_points",
            "score_percentage",
            "feedback",
        ]

    def get_feedback(self, obj):
        if obj.is_published:
            return obj.feedback

    def get_score(self, obj):
        request = self.context.get("request")
        if request and request.user.user_type == "STUDENT" and not obj.is_published:
            return None
        return obj.score

    def get_score_percentage(self, obj):
        request = self.context.get("request")
        if request and request.user.user_type == "STUDENT" and not obj.is_published:
            return None
        return obj.score_percentage

    def get_formatted_grade(self, obj):
        request = self.context.get("request")
        if request and request.user.user_type == "STUDENT" and not obj.is_published:
            return None
        return obj.formatted_grade

    def get_submission_status(self, obj):
        return "SUBMITTED"

    def get_grade_status(self, obj):
        if obj.graded_at and obj.is_published:
            return "GRADED"
        return "NOT GRADED"

    def get_grade_letter(self, obj):
        if obj and obj.score_percentage is not None and obj.is_published:
            from students.services import get_grade_details

            grade_details = get_grade_details(obj.score_percentage)
            return grade_details.get("letter_grade")
        return None

    # def get_assignment_status(self, obj):
    #     "To check if student submitted for this assignment"
    #     # submission = self._get_submission(obj)
    #     if obj:
    #         return "Submitted"

    #     if obj.assignment.due_date and obj.assignment.due_date < timezone.now():
    #         return "Overdue"

    #     return "Pending"


class StudentSubmissionGradeUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = StudentSubmission
        fields = [
            "id",
            "score",
        ]


class StudentSubmissionTeacherFeedbackSerializer(serializers.ModelSerializer):
    class Meta:
        model = StudentSubmission
        fields = [
            "id",
            "formatted_grade",
            "answers",
        ]


class StudentSubmissionGradeAsyncSerializer(serializers.Serializer):
    """Serializer for async grade engine task ID"""

    submission_id = serializers.UUIDField(read_only=True)
    task_id = serializers.UUIDField(read_only=True)
    message = serializers.CharField(read_only=True)


class StudentSubmissionUploadAsyncSerializer(serializers.Serializer):
    """Serializer for async upload answers task ID"""

    task_id = serializers.UUIDField(read_only=True)
    message = serializers.CharField(read_only=True)


class StudentSubmissionFormattedGradeAsyncSerializer(serializers.Serializer):
    """Serializer for async formatted grade task ID"""

    submission_id = serializers.UUIDField(read_only=True)
    task_id = serializers.UUIDField(read_only=True)
    message = serializers.CharField(read_only=True)


class StudentListSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(source="get_full_name", read_only=True)
    enrolled_courses = serializers.SerializerMethodField()
    course_description = serializers.SerializerMethodField()
    teacher = serializers.SerializerMethodField()
    grade = serializers.SerializerMethodField()
    total_assignments_in_course = serializers.SerializerMethodField()
    total_assignments_submitted = serializers.SerializerMethodField()
    percentage_of_submission = serializers.SerializerMethodField()
    email = serializers.SerializerMethodField()

    class Meta:
        model = CustomUser
        fields = [
            "id",
            "full_name",
            "email",
            "enrolled_courses",
            "course_description",
            "teacher",
            "grade",
            "total_assignments_in_course",
            "total_assignments_submitted",
            "percentage_of_submission",
        ]

    def get_email(self, obj):
        if obj.email and str(obj.email).endswith("@student.local"):
            return None
        return obj.email

    def _get_relevant_course(self, obj):
        request = self.context.get("request")
        if request:
            course_id = request.query_params.get("enrollments__course")
            if course_id:
                enrollment = obj.enrollments.filter(course_id=course_id).first()
                if enrollment:
                    return enrollment.course

            # fallback: first course taught by the authenticated user if teacher
            if (
                hasattr(request.user, "user_type")
                and request.user.user_type == "TEACHER"
            ):
                enrollment = obj.enrollments.filter(
                    course__teacher=request.user
                ).first()
                if enrollment:
                    return enrollment.course

        enrollment = obj.enrollments.first()
        return enrollment.course if enrollment else None

    def get_enrolled_courses(self, obj):
        return obj.enrollments.values_list("course__name", flat=True)

    def get_course_description(self, obj):
        course = self._get_relevant_course(obj)
        return course.description if course else None

    def get_teacher(self, obj):
        course = self._get_relevant_course(obj)
        if course and course.teacher:
            return f"{course.teacher.first_name} {course.teacher.last_name}"
        return None

    def get_grade(self, obj):
        course = self._get_relevant_course(obj)
        if course:
            enrollment = obj.enrollments.filter(course=course).first()
            if enrollment and enrollment.final_grade is not None:
                letter, gpa, remark = get_grade_details(enrollment.final_grade)
                return {
                    "letter_grade": letter,
                    "gpa": gpa,
                    "remark": remark,
                    "percentage": enrollment.final_grade,
                }
        return None

    def get_total_assignments_in_course(self, obj):
        course = self._get_relevant_course(obj)
        if course:
            return course.assignments.count()
        return 0

    def get_total_assignments_submitted(self, obj):
        course = self._get_relevant_course(obj)
        if course:
            return obj.submissions.filter(assignment__course=course).count()
        return 0

    def get_percentage_of_submission(self, obj):
        total = self.get_total_assignments_in_course(obj)
        if total == 0:
            return 0.0
        submitted = self.get_total_assignments_submitted(obj)
        return round((submitted / total) * 100, 2)
