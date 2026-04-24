# Email Template Drafts

This document contains recommended sample copy for each live email flow currently used in the platform. The tone is intentionally confident, clear, and inviting.

## Shared Style

- Voice: direct, warm, and professional
- Brand promise: efficient, modern, dependable
- CTA language: action-oriented and reassuring
- Footer line: `Need help? Contact us at {{ support_email }}.`

## 1. Account Activation

Current trigger:
`users/services.py`

Suggested subject:
`Activate your GradeA+ account`

Suggested preview text:
`Verify your email and get started with faster, smarter grading.`

Suggested CTA:
`Activate My Account`

Sample body:

```text
Hello {{ name }},

Welcome to GradeA+.

Your account is ready. Confirm your email address to activate your access and start managing grading, submissions, and course activity with confidence.

Use the button below to verify your email:
{{ activation_url }}

This link expires in {{ expiration_duration }} minutes.

If you did not create this account, you can safely ignore this email.

The GradeA+ Team
Need help? Contact us at {{ support_email }}.
```

## 2. Password Reset OTP

Current trigger:
`users/views.py` in `otp()` for `RESET_PASSWORD`

Suggested subject:
`Your GradeA+ password reset code`

Suggested preview text:
`Use this secure code to reset your password.`

Sample body:

```text
Hello {{ user_name|default:"there" }},

We received a request to reset your GradeA+ password.

Your password reset code is:
{{ otp_code }}

Enter this code in the app to continue. If you did not request a password reset, you can ignore this email and your account will remain secure.

The GradeA+ Team
Need help? Contact us at {{ support_email }}.
```

## 3. Password Change OTP

Current trigger:
`users/views.py` in `request_change_password()`

Suggested subject:
`Your GradeA+ password change code`

Suggested preview text:
`Confirm your password change with this one-time code.`

Sample body:

```text
Hello {{ user_name|default:"there" }},

You are one step away from updating your GradeA+ password.

Your password change code is:
{{ otp_code }}

Enter this code to complete the update. If you did not request this change, please secure your account immediately.

The GradeA+ Team
Need help? Contact us at {{ support_email }}.
```

## 4. Existing Student Enrollment

Current trigger:
`classrooms/views.py` using `templates/email/existing_student_course_enrollment.html`

Suggested subject:
`You have been added to {{ course.name }}`

Suggested preview text:
`Your course access is ready. Sign in and get started.`

Suggested CTA:
`Open My Course`

Sample body:

```text
Hello {{ student.get_full_name }},

You have been added to {{ course.name }} by {{ teacher.get_full_name }}.

Your access is already active, so you can sign in now and start participating right away.

Course details:
- Course: {{ course.name }}
- Teacher: {{ teacher.get_full_name }}
- Description: {{ course.description|default:"Course information is now available in your dashboard." }}

Open your dashboard here:
{{ login_url }}

We are glad to have you in the course.

The GradeA+ Team
Questions about the course? Contact {{ teacher.email }}.
```

## 5. New Student Course Registration

Current trigger:
`classrooms/views.py` using `templates/email/student_course_registration.html`

Suggested subject:
`Complete your registration for {{ course.name }}`

Suggested preview text:
`Your course invitation is ready. Finish setup and join your class.`

Suggested CTA:
`Complete Registration`

Sample body:

```text
Hello,

{{ teacher.get_full_name }} has invited you to join {{ course.name }} on GradeA+.

Your student access has been prepared. Complete your registration to create your password, set up your profile, and enter the course with confidence.

Finish your registration here:
{{ registration_link }}

This invitation link expires in 7 days.

If you were not expecting this invitation, you can ignore this email.

The GradeA+ Team
Questions about this course? Contact {{ teacher.email }}.
```

## 6. Student Removed From Course

Current trigger:
`classrooms/views.py` using `templates/email/student_course_removal.html`

Suggested subject:
`Your access to {{ course.name }} has been updated`

Suggested preview text:
`You are no longer enrolled in this course.`

Sample body:

```text
Hello {{ student.first_name }},

Your enrollment in {{ course.name }} has been removed.

Course details:
- Course: {{ course.name }}
- Teacher: {{ teacher.get_full_name }}

If this update was unexpected or you need clarification, please contact your teacher directly at {{ teacher.email }}.

The GradeA+ Team
```

## 7. Student Registration Link Renewal

Current trigger:
`classrooms/views.py` using `templates/email/student_token_renewal.html`

Suggested subject:
`Your new registration link for {{ course.name }}`

Suggested preview text:
`Your previous link expired. Here is a new one so you can complete setup.`

Suggested CTA:
`Complete Registration`

Sample body:

```text
Hello,

Your registration link for {{ course.name }} has been renewed.

You can now return and complete your account setup with the new link below:
{{ registration_link }}

This link expires in 7 days.

Once registration is complete, you will have access to your course and student dashboard.

The GradeA+ Team
Questions about this course? Contact {{ teacher.email }}.
```

## 8. Teacher Notification: Registration Link Renewed

Current trigger:
`classrooms/views.py` using `templates/email/teacher_token_renewal_notification.html`

Suggested subject:
`Registration link renewed for {{ student_email }}`

Suggested preview text:
`A fresh enrollment link has been sent to your student.`

Sample body:

```text
Hello {{ teacher.get_full_name }},

A new registration link has been sent to {{ student_email }} for {{ course.name }}.

The renewed link will remain active until {{ expiry_date|date:"F j, Y" }}.

No action is needed from you right now. Once the student completes registration, they can proceed into the course normally.

Thank you for keeping your class moving forward.

The GradeA+ Team
```

## 9. Teacher Notification: Student Submission

Current trigger:
`students/services.py` using `templates/email/student_submission_notification.html`

Suggested subject:
`New submission received for {{ assignment.title|default:course.name }}`

Suggested preview text:
`A student has submitted work and it is ready for review.`

Suggested CTA:
`Review Submission`

Sample body:

```text
Hello {{ teacher.get_full_name }},

{{ student.get_full_name }} has submitted work for {{ assignment.title|default:"your assignment" }} in {{ course.name }}.

Submission details:
- Student: {{ student.get_full_name }}
- Course: {{ course.name }}
- Assignment: {{ assignment.title|default:"Untitled Assignment" }}
- Submitted: {{ submission.submission_date|date:"F j, Y, g:i a" }}

Review the submission from your GradeA+ dashboard when you are ready.

The GradeA+ Team
```

## 10. Assignment Due Reminder for Students

Current trigger:
`assignments/tasks.py` using `templates/email/assignment_due_reminder.html` with `is_teacher=False`

Suggested subject:
`Reminder: {{ assignment.title|default:"Your assignment" }} is due in {{ reminder_label }}`

Suggested preview text:
`Stay on track and submit before the deadline.`

Suggested CTA:
`View Assignment`

Sample body:

```text
Hello {{ recipient.get_full_name }},

This is a reminder that {{ assignment.title|default:"your assignment" }} for {{ course.name }} is due in {{ reminder_label }}.

Due date:
{{ due_date_display }}

Now is a good time to review your work, make any final updates, and submit before the deadline.

Stay focused. You are almost there.

The GradeA+ Team
```

## 11. Assignment Due Reminder for Teachers

Current trigger:
`assignments/tasks.py` using `templates/email/assignment_due_reminder.html` with `is_teacher=True`

Suggested subject:
`Reminder: {{ assignment.title|default:"Assignment" }} closes in {{ reminder_label }}`

Suggested preview text:
`Your assignment deadline is approaching.`

Sample body:

```text
Hello {{ recipient.get_full_name }},

This is a reminder that {{ assignment.title|default:"an assignment" }} in {{ course.name }} is due in {{ reminder_label }}.

Due date:
{{ due_date_display }}

Your class deadline is approaching, and everything is set for a smooth submission window.

You can review the assignment from your dashboard if you would like to make any final checks.

The GradeA+ Team
```

## Implementation Notes

- The account activation email currently uses a provider template ID in `users/services.py`, so this draft is best used as the source copy for that external template.
- The password reset and password change flows currently send plain text only. If you want, these drafts can be turned into matching HTML templates next.
- The assignment due reminder template is shared by students and teachers, but the copy should stay slightly different for each audience.
