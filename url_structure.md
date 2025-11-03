
## Authentication

POST    /api/v1/auth/login/
POST    /api/v1/auth/logout/
POST    /api/v1/auth/register/
GET     /api/v1/auth/me/


## Session

GET     /api/v1/sessions/                     # List all sessions
POST    /api/v1/sessions/                     # Create session
GET     /api/v1/sessions/{id}/               # Get session details
PATCH   /api/v1/sessions/{id}/               # Update session
DELETE  /api/v1/sessions/{id}/               # Delete session
GET     /api/v1/sessions/{id}/courses/       # List courses in session


## Courses

GET     /api/v1/courses/                      # List all courses
POST    /api/v1/courses/                      # Create course
GET     /api/v1/courses/{id}/                # Get course details
PATCH   /api/v1/courses/{id}/                # Update course
DELETE  /api/v1/courses/{id}/                # Delete course
POST    /api/v1/courses/{id}/students/       # Add students to course
GET     /api/v1/courses/{id}/assignments/    # List course assignments
GET     /api/v1/courses/{id}/submissions/    # List course submissions


## Assignment

GET     /api/v1/assignments/                  # List all assignments
POST    /api/v1/assignments/                  # Create assignment
GET     /api/v1/assignments/{id}/            # Get assignment details
PATCH   /api/v1/assignments/{id}/            # Update assignment
DELETE  /api/v1/assignments/{id}/            # Delete assignment
POST    /api/v1/assignments/generate/         # Generate AI assignment
GET     /api/v1/assignments/{id}/submissions/ # List submissions for assignment


## Student Submissions

GET     /api/v1/submissions/                  # List all submissions
POST    /api/v1/submissions/                  # Create submission
GET     /api/v1/submissions/{id}/            # Get submission details
PATCH   /api/v1/submissions/{id}/            # Update submission
DELETE  /api/v1/submissions/{id}/            # Delete submission
POST    /api/v1/submissions/{id}/grade/      # Grade submission with AI


## User

GET     /api/v1/users/                        # List all users
POST    /api/v1/users/                        # Create user
GET     /api/v1/users/{id}/                  # Get user details
PATCH   /api/v1/users/{id}/                  # Update user
DELETE  /api/v1/users/{id}/                  # Delete user
GET     /api/v1/users/{id}/courses/          # List user's courses
GET     /api/v1/users/{id}/submissions/      # List user's submissions
