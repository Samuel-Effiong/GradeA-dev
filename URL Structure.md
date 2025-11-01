### Authentication

POST    /api/v1/users/auth/login/                    # User login
POST    /api/v1/users/auth/logout/                   # User logout
POST    /api/v1/users/auth/register/                 # User registration
POST    /api/v1/users/auth/refresh/                  # Refresh JWT token
POST    /api/v1/users/auth/register/student          # Student registration

POST    /api/v1/users/auth/change-password/          # Change password
POST    /api/v1/users/auth/reset-password/           # Reset password with OTP

POST    /api/v1/users/auth/otp   # Request OTP

POST    /api/v1/users/auth/reset-password/    # Reset the password using an OTP

POST    /api/v1/users/auth/verify/       # Verify email and activate account





### Users

GET     /api/v1/users/                         # List all users (SuperAdmin only)
POST    /api/v1/users/                         # Create new user
GET     /api/v1/users/me/                      # Get current user profile
GET     /api/v1/users/{id}/                    # Get user details
PATCH   /api/v1/users/{id}/                    # Update user
DELETE  /api/v1/users/{id}/                    # Delete user





### Student Submissions

GET     /api/v1/submissions/?assignment                   # List all submissions

POST    /api/v1/submissions/                   # Create new submission (Student only)

GET     /api/v1/submissions/{id}/              # Get submission details

PATCH   /api/v1/submissions/{id}/              # Update submission

DELETE  /api/v1/submissions/{id}/              # Delete submission

POST    /api/v1/submissions/{assignment_id}/upload/       # Upload answer file

POST    /api/v1/submissions/{id}/grade/        # Grade submission (Teacher only)



### Courses

GET     /api/v1/courses/?session&is_active&categories        # List all courses  POST    /api/v1/courses/                      # Create a new course

GET     /api/v1/courses/{id}/                 # Get course details

PATCH   /api/v1/courses/{id}/                 # Update course

DELETE  /api/v1/courses/{id}/                 # Delete course

POST    /api/v1/courses/{id}/students/        # Add student to course

GET     /api/v1/courses/{id}/students/        # List students in course

DELETE  /api/v1/courses/{course_id}/students/{student_id}/  # Remove student from course

POST  /api/v1/courses/renew-student-token/  # Renew expired student activation token



### Sessions

GET     /api/v1/sessions/                     # List all academic sessions
POST    /api/v1/sessions/                     # Create new academic session

GET     /api/v1/sessions/{id}/                # Get session details
PATCH   /api/v1/sessions/{id}/                # Update session
DELETE  /api/v1/sessions/{id}/                # Delete session



### Student Courses

GET     /api/v1/student-courses/              # List all student enrollments
POST    /api/v1/student-courses/              # Create new enrollment

GET     /api/v1/student-courses/{id}/         # Get enrollment details
PATCH   /api/v1/student-courses/{id}/         # Update enrollment
DELETE  /api/v1/student-courses/{id}/         # Delete enrollment





### Assignments

GET     /api/v1/assignments/?course&assignment_type         # List all assignments
POST    /api/v1/assignments/                  # Create new assignment (Teacher only)

GET     /api/v1/assignments/{id}/             # Get assignment details
PATCH   /api/v1/assignments/{id}/             # Update assignment (Teacher Only)
DELETE  /api/v1/assignments/{id}/             # Delete assignment  (Teacher Only)

POST    /api/v1/assignments/generate/         # Generate AI assignment  (Teacher Only)
POST    /api/v1/assignments/generate/{course_id}    # Generate assignment using prompt  (Teacher Only)

POST    /api/v1/assignments/upload/           # Upload assignment file  (Teacher Only)
