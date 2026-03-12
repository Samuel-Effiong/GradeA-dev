# Test Documentation: Classrooms Models

This file describes the comprehensive tests implemented for the School, Session, and Course models in `classrooms/tests.py`.

## School Model Tests (SchoolModelTest)
The purpose of these tests is to ensure that the School model correctly stores basic educational institution information.

1.  **test_school_creation**: Verifies that a School instance can be created with all fields (name, address, phone, website) and that they are stored correctly.
2.  **test_school_str_representation**: Ensures that the `__str__` method returns the school's name, which is important for the Django admin and other displays.
3.  **test_school_name_uniqueness**: Validates that the database enforces unique names for schools, preventing duplicate entries.
4.  **test_school_optional_fields**: Confirms that only the `name` field is required, and other fields like `address`, `phone`, and `website` can be left empty.

## Session Model Tests (SessionModelTest)
These tests focus on the Session model, which represents academic periods.

1.  **test_session_creation**: Ensures that a Session is correctly linked to a teacher and stores its name.
2.  **test_session_name_uniqueness_per_teacher**: Tests the `UniqueConstraint` ensuring a teacher cannot have two sessions with the same name, but different teachers can use the same session name (e.g., both having a "Fall 2024" session).
3.  **test_session_ordering**: Verifies that sessions are ordered by their creation date in descending order (newest first) as defined in the model's Meta class.

## Course Model Tests (CourseModelTest)
These tests ensure that the Course model correctly manages the grouping of students within sessions.

1.  **test_course_creation**: Verifies that a Course is correctly created and linked to both a teacher and a session. It also checks that the `is_active` default value is `True`.
2.  **test_course_str_representation**: Checks that the `__str__` method correctly formats the string as "Session Name - Course Name".
3.  **test_course_uniqueness_per_session**: Validates the complex `UniqueConstraint` involving the course name, teacher, and session. It ensures that the same course name cannot be repeated by the same teacher within the same session.
4.  **test_course_null_teacher_or_session**: Confirms that courses can be created without being immediately assigned to a teacher or session, allowing for flexible course setup.
5.  **test_course_ordering**: Ensures that courses are ordered alphabetically by their name as defined in the model's Meta class.

## View and API Tests (classrooms/test_views.py)
These tests verify the API endpoints, permissions, caching, and robust error handling.

### School ViewSet Tests
1.  **test_list_schools_superadmin_caching_and_invalidation**: Ensures that schools are cached for superadmins, but the cache is invalidated when a new school is created via the API. This uses `mock.patch` on `cache.delete_pattern` to verify the signal-driven invalidation.
2.  **test_list_schools_teacher_denied**: Validates that teachers are forbidden from listing schools (access control), ensuring that sensitive institutional data is not exposed.

### Session ViewSet Tests
1.  **test_session_cache_invalidation_on_creation**: Verifies that the session list is cached but immediately refreshed when a new session is added. This ensures teachers always see their most up-to-date academic periods.
2.  **test_session_isolation**: Ensures that teachers can only see their own sessions. This is a critical privacy feature to prevent data leakage between instructors.
3.  **test_hacker_access_others_session**: Tests that attempting to access a session belonging to another teacher returns a 404 (Hacker scenario), proving that the system correctly filters by ownership even when a direct ID is provided.

### Course ViewSet Tests
1.  **test_course_cache_invalidation_on_creation**: Confirms that the course list is cached and properly invalidated upon creation of a new course by clearing the `courses:list` cache.
2.  **test_hacker_enroll_same_student_twice**: Ensures the system prevents double enrollment of the same student in a course, returning a 400 error. This prevents data corruption and redundant notifications.
3.  **test_hacker_modify_others_course**: Validates that a teacher cannot modify or even see (404) courses belonging to other teachers, enforcing strong data isolation.
4.  **test_hacker_malformed_data**: Checks that the API robustly handles invalid or malformed data (like empty names or non-UUID session IDs) with 400 errors instead of crashing with a 500 error.

## Cache Invalidation Architecture (classrooms/signals.py)
The system uses a robust signal-based architecture to maintain cache consistency across the `classrooms` app.

### Signals and Patterns
Whenever a model is created, updated, or deleted, the following signals are triggered:
*   **School Signals**: Clears `*superadmin*`, `*schooladmin*`, and `*schools*` patterns.
*   **Session Signals**: Clears the above plus `*sessions*` and `*course*` patterns.
*   **Course Signals**: Clears all previous patterns plus `*assignments*`, as courses are the foundation for assignments.
*   **StudentCourse Signals**: Clears all previous patterns plus `*studentcourses*`.
*   **Topic Signals**: Clears all previous patterns plus `*topics*`.

This cascading invalidation ensures that any change in the hierarchy (e.g., adding a session) correctly refreshes all dependent cached lists (e.g., the schools list which might show session counts, or the course lists).
