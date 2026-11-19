# Departments: wording to approve

Thank you for answering the ten questions. Your answers are built into the plan. What is left is the wording below. Each item says where the person sees it and gives the exact words. Please say "approved" or tell us what to change.

## What you decided (for the record)

Departments keep working when a school's license lapses; they stop for a teacher only when that teacher is removed from the school, or is left off when the school gets a new license. Each teacher has one edit switch that covers both department lessons and the shared library. The school admin shows in the people list as "School admin". Deleting a department that has content makes the admin choose between deleting the content and moving it. A teacher who left and returns is not put back automatically. Only our internal admin tools can change departments for a school. Any admin of the school can manage its departments. Teachers get an email when they are added to, or removed from, a department. No two departments in a school share a name. Offline contracts work as they do today: an admin ends them by hand.

## The wording

**1. When the name is already used.** The admin sees: Your school already has a department called "{name}". They then choose a different name.

**2. When the name is empty or too long.** The admin sees: Enter a department name of 1 to 100 characters.

**3. When the teacher is already in the department.** The admin sees: This teacher is already in this department.

**4. When the admin deletes a department that still has content.** The admin sees: This department still has content. Choose to delete it or move it to another department. They then pick one.

**5. When the place to move the content to is not allowed.** The admin sees: Choose a different department at your school to move the content to.

**6. When a department has too many teachers.** The admin sees: A department can have up to 500 teachers. (This is only a safety limit.) They can split the group.

**7. Activity log, for a change to a department.** The log shows the kind of thing that changed as: Department

**8. Activity log, for a change to one teacher's place in a department.** The log shows the kind of thing that changed as: Department member

**9. Roles in the people list.** Each person shows as either: School admin, or Teacher

**10. When a teacher's editing has been turned off.** Used by the lessons and library pieces that come later. The teacher sees: Your school admin has turned off editing for you in this department. You can still read everything.

**11. The email a teacher gets when they are added to a department.**
Subject: You've been added to the {department} department
Message: A school admin at {school} has added you to the {department} department on Grade A+. You can see the department and the people in it the next time you open the app. Your courses, students, grades and credits are not shared with anyone in the department. Questions? Ask your school admin.

**12. The email a teacher gets when they are removed from a department.**
Subject: You've been removed from the {department} department
Message: A school admin at {school} has removed you from the {department} department on Grade A+. You no longer see this department. Your courses, students, grades and credits are not affected. If you think this was a mistake, ask your school admin.

The email greeting uses the teacher's name, as our other emails do. Only the school name and the department name change from email to email.

**13. When an admin tries to add more than 50 teachers in one step.** The admin sees: You can add up to 50 teachers at a time. Add the rest in another step.

**About the emails.** If an admin adds 50 teachers at once, 50 emails are sent, one per teacher, a moment after the change is saved. An email problem never stops or undoes the change; a failed send is tried again by itself. We do not send an email when a teacher leaves the school, when a whole department is deleted, or when only the edit switch changes. Tell us if you want any of those too.

Existing messages stay as they are, for example "Resource not found" and "You must be a school admin to access this endpoint." Existing log labels such as "Department created" and "Permission changed" stay too. Screen texts (delete confirmation, empty state, success messages) are approved with the screens.

## Time

About 15 working days (between 14 and 16), roughly three weeks, to build and test. That is one day more than before because of the emails. Most of the time goes into proving that one teacher can never see another teacher's data.
