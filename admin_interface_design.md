# Three-Level Admin Interface Design

## Overview
This document outlines the design for a three-level admin interface for the Grade Automator Plus system. The levels are:

1. **Superadmin** - Can view information across all levels and schools
2. **School Admin** - Can only see details about teachers, courses, students, etc. within their school
3. **Teacher Admin** - Can only see information relevant to their courses, students, assignments, etc.

## Data Model Changes

### User Types
Add two new user types to the `UserTypes` class in `users/models.py`:
```python
class UserTypes(models.TextChoices):
    STUDENT = "STUDENT", "Student"
    TEACHER = "TEACHER", "Teacher"
    SCHOOL_ADMIN = "SCHOOL_ADMIN", "School Admin"
    SUPERADMIN = "SUPERADMIN", "Superadmin"
```

### School Model
Uncomment and implement the `School` model in `classrooms/models.py`:
```python
class School(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True, verbose_name=_("Email address"))
    name = models.CharField(max_length=255, unique=True)
    address = models.TextField(blank=True, null=True)
    phone = models.CharField(max_length=20, blank=True, null=True)
    website = models.URLField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name
```

### Link Teachers and Students to Schools
Add a `school` field to the `CustomUser` model in `users/models.py`:
```python
school = models.ForeignKey(
    "classrooms.School",
    on_delete=models.SET_NULL,
    null=True,
    blank=True,
    related_name="users"
)
```

## Permission Classes
Create new permission classes in `users/permissions.py`:
```python
class IsSuperAdmin(BasePermission):
    """
    Allows access only to superadmins
    """
    def has_permission(self, request, view):
        return bool(
            request.user and
            request.user.is_authenticated and
            request.user.user_type == UserTypes.SUPERADMIN
        )

class IsSchoolAdmin(BasePermission):
    """
    Allows access only to school admins
    """
    def has_permission(self, request, view):
        return bool(
            request.user and
            request.user.is_authenticated and
            request.user.user_type == UserTypes.SCHOOL_ADMIN
        )

class IsTeacherAdmin(BasePermission):
    """
    Allows access only to teacher admins (teachers)
    """
    def has_permission(self, request, view):
        return bool(
            request.user and
            request.user.is_authenticated and
            request.user.user_type == UserTypes.TEACHER
        )
```

## KPIs and Statistics

### Superadmin Level
1. **Overall System Statistics**
   - Total number of schools
   - Total number of active teachers
   - Total number of active students
   - Total number of active courses
   - Total number of assignments
   - Total number of submissions

2. **School Performance Metrics**
   - Number of teachers per school
   - Number of students per school
   - Number of courses per school
   - Average student performance per school
   - Assignment completion rates per school

3. **Teacher Performance Metrics**
   - Number of courses per teacher
   - Number of students per teacher
   - Average student performance per teacher
   - Assignment completion rates per teacher

4. **Student Performance Metrics**
   - Average grades across all students
   - Assignment completion rates across all students
   - Distribution of grades (A, B, C, D, F) across all students

### School Admin Level
1. **School Statistics**
   - Number of active teachers in the school
   - Number of active students in the school
   - Number of active courses in the school
   - Number of assignments in the school
   - Number of submissions in the school

2. **Teacher Performance Metrics**
   - Number of courses per teacher
   - Number of students per teacher
   - Average student performance per teacher
   - Assignment completion rates per teacher

3. **Student Performance Metrics**
   - Average grades across school students
   - Assignment completion rates across school students
   - Distribution of grades (A, B, C, D, F) across school students

4. **Course Performance Metrics**
   - Average grades per course
   - Assignment completion rates per course
   - Student enrollment per course

### Teacher Admin Level
1. **Teacher Statistics**
   - Number of active courses
   - Number of active students
   - Number of assignments
   - Number of submissions

2. **Course Performance Metrics**
   - Average grades per course
   - Assignment completion rates per course
   - Student enrollment per course

3. **Student Performance Metrics**
   - Average grades per student
   - Assignment completion rates per student
   - Individual student progress

4. **Assignment Performance Metrics**
   - Average grades per assignment
   - Completion rates per assignment
   - Question-level performance analysis

## Endpoints

### Superadmin Endpoints
1. `/api/admin/dashboard/`
   - GET: Overall system statistics

2. `/api/admin/schools/`
   - GET: List of all schools
   - POST: Create a new school
   - PUT/PATCH: Update a school
   - DELETE: Delete a school

3. `/api/admin/schools/{school_id}/`
   - GET: School details
   - PUT/PATCH: Update a school
   - DELETE: Delete a school

4. `/api/admin/schools/{school_id}/teachers/`
   - GET: List of teachers in a school

5. `/api/admin/schools/{school_id}/students/`
   - GET: List of students in a school

6. `/api/admin/schools/{school_id}/courses/`
   - GET: List of courses in a school

7. `/api/admin/teachers/`
   - GET: List of all teachers
   - POST: Create a new teacher
   - PUT/PATCH: Update a teacher
   - DELETE: Delete a teacher

8. `/api/admin/students/`
   - GET: List of all students
   - POST: Create a new student
   - PUT/PATCH: Update a student
   - DELETE: Delete a student

9. `/api/admin/courses/`
   - GET: List of all courses

10. `/api/admin/assignments/`
    - GET: List of all assignments

11. `/api/admin/submissions/`
    - GET: List of all submissions

### School Admin Endpoints
1. `/api/school-admin/dashboard/`
   - GET: School statistics

2. `/api/school-admin/teachers/`
   - GET: List of teachers in the school
   - POST: Create a new teacher in the school
   - PUT/PATCH: Update a teacher in the school
   - DELETE: Delete a teacher from the school

3. `/api/school-admin/students/`
   - GET: List of students in the school
   - POST: Create a new student in the school
   - PUT/PATCH: Update a student in the school
   - DELETE: Delete a student from the school

4. `/api/school-admin/courses/`
   - GET: List of courses in the school

5. `/api/school-admin/assignments/`
   - GET: List of assignments in the school

6. `/api/school-admin/submissions/`
   - GET: List of submissions in the school

### Teacher Admin Endpoints
1. `/api/teacher-admin/dashboard/`
   - GET: Teacher statistics

2. `/api/teacher-admin/courses/`
   - GET: List of courses taught by the teacher
   - POST: Create a new course
   - PUT/PATCH: Update a course
   - DELETE: Delete a course

3. `/api/teacher-admin/courses/{course_id}/students/`
   - GET: List of students in a course
   - POST: Add a student to a course
   - DELETE: Remove a student from a course

4. `/api/teacher-admin/assignments/`
   - GET: List of assignments created by the teacher
   - POST: Create a new assignment
   - PUT/PATCH: Update an assignment
   - DELETE: Delete an assignment

5. `/api/teacher-admin/assignments/{assignment_id}/submissions/`
   - GET: List of submissions for an assignment

6. `/api/teacher-admin/students/`
   - GET: List of students taught by the teacher

7. `/api/teacher-admin/students/{student_id}/submissions/`
   - GET: List of submissions by a student

## Resources Required

### Models
1. School
2. CustomUser (with new user types and school field)

### Serializers
1. SchoolSerializer
2. SuperadminDashboardSerializer
3. SchoolAdminDashboardSerializer
4. TeacherAdminDashboardSerializer

### Views
1. SuperadminDashboardViewSet
2. SchoolAdminDashboardViewSet
3. TeacherAdminDashboardViewSet
4. SchoolViewSet
5. SchoolTeacherViewSet
6. SchoolStudentViewSet
7. SchoolCourseViewSet

### Permissions
1. IsSuperAdmin
2. IsSchoolAdmin
3. IsTeacherAdmin

## Implementation Steps
1. Update the `UserTypes` class to include new user types
2. Implement the `School` model
3. Add a `school` field to the `CustomUser` model
4. Create new permission classes
5. Implement serializers for the new models and dashboard data
6. Implement viewsets for the new endpoints
7. Update the URL configuration to include the new endpoints
8. Test the new endpoints with different user types
9. Document the new endpoints and their usage
