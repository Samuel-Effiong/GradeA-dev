"""Roster-related outbound email.

Every function here builds a MailerLite merge payload and hands it to
`safe_delay`, so a broker outage degrades to "the enrollment happened but
the notification didn't" rather than failing the teacher's request. None of
them raise.

Extracted verbatim from `classrooms.views` during the section-3 audit: the
subject lines, template ids and merge keys are the contract MailerLite's
templates render against, so they are reproduced exactly rather than tidied.
"""

import logging

from django.conf import settings
from django.template.loader import render_to_string
from django.utils import timezone

from AutoGrader.dispatch import safe_delay
from AutoGrader.tasks import send_email_task

logger = logging.getLogger(__name__)

# MailerLite template ids. Named so a call site reads as intent rather than
# as an opaque string.
TEMPLATE_COURSE_NOTICE = "yzkq340r0n04d796"
TEMPLATE_ACTIVATION_INVITE = "ynrw7gy0ye2l2k8e"


def _base_merge_data(title, name):
    return {
        "title": title,
        "name": name,
        "current_year": timezone.now().year,
        "support_email": settings.SUPPORT_EMAIL,
    }


def student_registration_link(activation_token, email):
    domain = settings.STUDENT_FRONTEND_DOMAIN
    return f"https://{domain}/register/student/{activation_token}?email={email}"


def send_added_to_course_email(student, course):
    """Tell an already-active student they've been added to a course."""
    login_url = f"https://{settings.STUDENT_FRONTEND_DOMAIN}"
    content = f"""
                        You have been added to {course.name} by {course.teacher.get_full_name()}<br><br>

                        Your access is already active, so you can sign in now and start participating right away.
                        <br><br>

                        Course Details:<br>
                        - Course: {course.name}<br>
                        - Teacher: {course.teacher.get_full_name()}<br>
                        - Description: {course.description}<br><br>

                        Open your dashboard here:<br>
                        {login_url}<br><br>

                        We are glad to have you in the course.<br><br>

                        Questions about the course? Contact {course.teacher.email}.
                        """

    merge_data = _base_merge_data(
        f"You have been added to {course.name}", student.get_full_name()
    )
    merge_data["content"] = content

    safe_delay(
        send_email_task,
        subject=f"You have been added to {course.name}",
        message="",
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[student.email],
        html_message=None,
        template_id=TEMPLATE_COURSE_NOTICE,
        merge_data=merge_data,
    )


def send_course_invitation_email(student, course, activation_token):
    """Invite a not-yet-activated student to finish registration."""
    registration_link = student_registration_link(activation_token, student.email)

    top_content = f"""
                        {course.teacher.get_full_name()} has invited you to join {course.name} on Grade A+ <br><br>

                        Your student access has been prepared. Complete your registration to create your password,
                        set up your profile, and enter the course with confidence.<br><br>

                        Finish your registration here:<br>
                        """

    bottom_content = f"""
                        This invitation link expires in 24 hours.<br><br>

                        If you were not expecting this invitation, you can ignore this email.<br><br>
                        Questions about this course? Contact {course.teacher.email}.<br><br>
                        """

    merge_data = _base_merge_data(
        f"Complete your registration for {course.name}", student.get_full_name()
    )
    merge_data.update(
        {
            "top_content": top_content,
            "bottom_content": bottom_content,
            "activation_url": registration_link,
        }
    )

    safe_delay(
        send_email_task,
        subject="Your course invitation is ready. Finish setup and join your class",
        message="",
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[student.email],
        html_message=None,
        template_id=TEMPLATE_ACTIVATION_INVITE,
        merge_data=merge_data,
    )


def send_bulk_enrollment_email(student, course):
    """Bulk-import variant: shorter copy, same two templates.

    Deliberately kept distinct from the single-add emails above rather than
    merged with them - the bulk copy is terser on purpose, and collapsing
    them would silently change the wording of one flow or the other.
    """
    if student.is_active:
        merge_data = _base_merge_data(
            f"You have been added to {course.name}", student.get_full_name()
        )
        merge_data["content"] = (
            f"You have been added to {course.name} by {course.teacher.get_full_name()}.<br><br>\n\n"
            "Your access is already active."
        )
        safe_delay(
            send_email_task,
            subject=f"You have been added to {course.name}",
            message="",
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[student.email],
            template_id=TEMPLATE_COURSE_NOTICE,
            merge_data=merge_data,
        )
        return

    merge_data = _base_merge_data(
        f"Complete your registration for {course.name}", student.get_full_name()
    )
    merge_data.update(
        {
            "top_content": (
                f"{course.teacher.get_full_name()} has invited you to join "
                f"{course.name}."
            ),
            "bottom_content": "This invitation link expires in 24 hours.",
            "activation_url": student_registration_link(
                student.activation_token, student.email
            ),
        }
    )
    safe_delay(
        send_email_task,
        subject="Complete Your Registration for the Course",
        message="",
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[student.email],
        template_id=TEMPLATE_ACTIVATION_INVITE,
        merge_data=merge_data,
    )


def send_removed_from_course_email(student, course):
    """Tell a student their enrollment in a course has been removed."""
    content = f"""
                Your enrollment in {course.name} has been removed.<br><br>

                Course details:<br>
                - Course: {course.name}<br>
                - Teacher: {course.teacher.get_full_name()}<br><br>

                If you have any questions about this removal, please contact your teacher at {course.teacher.email}
                """

    merge_data = _base_merge_data(
        f"Your access to {course.name} has been updated", student.get_full_name()
    )
    merge_data["content"] = content

    safe_delay(
        send_email_task,
        subject="You are no longer enrolled in this course",
        message="",
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[student.email],
        html_message=None,
        template_id=TEMPLATE_COURSE_NOTICE,
        merge_data=merge_data,
    )


def send_token_renewal_emails(student, course, new_token, expiry_date):
    """Notify both sides that a student's activation link was reissued.

    These two use rendered Django templates rather than MailerLite ids,
    unlike every other notification here. Left as-is: changing which
    channel an email goes out on is a deliberate product change, not a
    refactor.
    """
    registration_link = (
        f"https://{settings.STUDENT_FRONTEND_DOMAIN}/register/student/{new_token}"
    )

    student_html = render_to_string(
        "email/student_token_renewal.html",
        context={
            "course": course,
            "teacher": course.teacher,
            "registration_link": registration_link,
        },
    )
    safe_delay(
        send_email_task,
        subject="Course Registration Link Renewed",
        message="",
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[student.email],
        html_message=student_html,
    )

    teacher_html = render_to_string(
        "email/teacher_token_renewal_notification.html",
        context={
            "teacher": course.teacher,
            "student_email": student.email,
            "course": course,
            "expiry_date": expiry_date,
        },
    )
    safe_delay(
        send_email_task,
        subject=f"Registration Link Renewed - {student.email}",
        message="",
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[course.teacher.email],
        html_message=teacher_html,
    )
