"""Every user-facing text of Epic F1, in one place (owner-approved
2026-11-19, W1-W13 of docs/evidence/epic-f1/OWNER_SHEET.md). A change to a
text is one edit here and needs the owner again.

Placeholders use str.format names. The two emails take the school and the
department name only.
"""

# W1 is the text of the department name taken reason
NAME_TAKEN = 'Your school already has a department called "{name}".'
# W2 is the text of the department name invalid reason
NAME_INVALID = "Enter a department name of 1 to 100 characters."
# W3 is the text of the department already member reason
ALREADY_MEMBER = "This teacher is already in this department."
# W4 is the text of the department content choice required reason
CONTENT_CHOICE_REQUIRED = (
    "This department still has content. Choose to delete it or move it to "
    "another department."
)
# W5 is the text of the department reassign target invalid reason
REASSIGN_TARGET_INVALID = (
    "Choose a different department at your school to move the content to."
)
# W6 is the text of the department too many teachers reason
TOO_MANY_TEACHERS = "A department can have up to 500 teachers."
# W7 and W8 are the activity log labels for the two target types
TARGET_LABEL_DEPARTMENT = "Department"
TARGET_LABEL_MEMBER = "Department member"
# W9 is the pair of roster roles
ROLE_SCHOOL_ADMIN = "School admin"
ROLE_TEACHER = "Teacher"
# W10 is shown by the lessons and library pieces later, never raised in F1
EDITING_OFF = (
    "Your school admin has turned off editing for you in this department. "
    "You can still read everything."
)
# W11 is the email a teacher gets when added
ADDED_SUBJECT = "You've been added to the {department} department"
ADDED_BODY = (
    "A school admin at {school} has added you to the {department} department "
    "on Grade A+. You can see the department and the people in it the next "
    "time you open the app. Your courses, students, grades and credits are "
    "not shared with anyone in the department. Questions? Ask your school "
    "admin."
)
# W12 is the email a teacher gets when removed
REMOVED_SUBJECT = "You've been removed from the {department} department"
REMOVED_BODY = (
    "A school admin at {school} has removed you from the {department} "
    "department on Grade A+. You no longer see this department. Your courses, "
    "students, grades and credits are not affected. If you think this was a "
    "mistake, ask your school admin."
)
# W13 is the text of the department batch too large reason
BATCH_TOO_LARGE = (
    "You can add up to 50 teachers at a time. Add the rest in another step."
)

MAX_TEACHERS_PER_ADD = 50
MAX_MEMBERS_PER_DEPARTMENT = 500
