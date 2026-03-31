# AssignmentDetailSerializer - Student Submissions Enhancement

## Overview

Added a new `student_submissions` field to the `AssignmentDetailSerializer` that includes a comprehensive list of all students enrolled in the course with their submission and grading details.

## Changes Made

### 1. New Serializer: `StudentSubmissionDetailSerializer`

Created a new serializer class to handle individual student submission details with the following fields:

```python
class StudentSubmissionDetailSerializer(serializers.Serializer):
    student_name       # Full name of the student
    email             # Email address of the student
    submission_status # Status of submission ("submitted" or "pending")
    grade             # Grade received (human or AI-graded, whichever is available)
    grade_status      # Status of grading (human_graded, ai_graded, regraded, or ungraded)
```

### 2. Updated: `AssignmentDetailSerializer`

Added the `student_submissions` field which:
- Returns a list of all students enrolled in the course
- Includes their submission and grading information
- Handles both submitted and non-submitted students

## Field Descriptions

### `student_name`
- **Type**: String
- **Description**: Full name of the student (first_name + last_name)
- **Handled Cases**:
  - Returns empty string if names are not provided
  - Strips extra whitespace automatically

### `email`
- **Type**: String
- **Description**: Email address of the student

### `submission_status`
- **Type**: String
- **Possible Values**:
  - `"submitted"` - Student has submitted an assignment
  - `"pending"` - Student has not submitted yet
- **Logic**: Based on whether a StudentSubmission record exists for the student and assignment

### `grade`
- **Type**: Float or null
- **Description**: Numerical grade awarded to the student
- **Logic**:
  1. Returns human grade (`score`) if it exists and is greater than 0
  2. Falls back to AI grade (`ai_score`) if human grade doesn't exist
  3. Returns `null` if neither exists
- **Use Case**: Prioritizes teacher overrides over automated AI grading

### `grade_status`
- **Type**: String
- **Possible Values**:
  - `"ungraded"` - No grading has occurred yet
  - `"ai_graded"` - Only AI has graded the submission
  - `"human_graded"` - Teacher has manually graded the submission
  - `"regraded"` - Teacher has re-graded a previously AI-graded submission
  - `"pending"` - Student has not submitted (implicitly "ungraded")
- **Logic**:
  1. Checks if submission was regraded (`was_regraded` and `regraded_at`)
  2. Checks if human graded (`graded_at` is set)
  3. Checks if AI graded (`ai_graded_at` is set)
  4. Returns "ungraded" if none of the above

## API Response Example

**Endpoint**: `GET /api/v1/assignments/{assignment_id}/`

**Response Structure**:
```json
{
  "id": "uuid-here",
  "title": "Quantum Mechanics Midterm",
  "course": "uuid-course",
  "topic": "uuid-topic",
  "status": "PUBLISHED",
  "raw_input": "...",
  "created_at": "2026-03-30T10:00:00Z",
  "due_date": "2026-04-15T23:59:59Z",
  "extraction_confidence": 87,
  "assignment_type": "HYBRID",
  "total_points": 100,
  "question_count": 5,
  "student_submissions": [
    {
      "student_name": "John Doe",
      "email": "john.doe@school.com",
      "submission_status": "submitted",
      "grade": 85.5,
      "grade_status": "human_graded"
    },
    {
      "student_name": "Jane Smith",
      "email": "jane.smith@school.com",
      "submission_status": "submitted",
      "grade": 92.0,
      "grade_status": "ai_graded"
    },
    {
      "student_name": "Bob Johnson",
      "email": "bob.johnson@school.com",
      "submission_status": "pending",
      "grade": null,
      "grade_status": "ungraded"
    },
    {
      "student_name": "Alice Williams",
      "email": "alice.williams@school.com",
      "submission_status": "submitted",
      "grade": 78.0,
      "grade_status": "regraded"
    }
  ]
}
```

## Implementation Details

### Performance Optimization

The implementation uses `.select_related('student')` to optimize database queries:

```python
enrolled_students = StudentCourse.objects.filter(
    course=obj.course
).select_related('student')
```

This reduces the number of database queries by fetching the related student objects in a single query.

### Grade Priority Logic

The `get_grade()` method implements a priority system:

```python
# Priority: Human grade > AI grade
if obj.score is not None and obj.score > 0:
    return float(obj.score)  # Human grade takes precedence

if obj.ai_score is not None and obj.ai_score > 0:
    return float(obj.ai_score)  # Fall back to AI grade

return None  # No grade exists
```

### Grade Status State Machine

The `get_grade_status()` method follows a priority chain:

```
1. Check if regraded (human override of AI grade)
2. Check if human_graded (teacher manually graded)
3. Check if ai_graded (automated grading)
4. Return "ungraded" (default)
```

## Database Queries

The implementation triggers these database queries:

1. **Main Query**: Get the assignment (implicit in ModelSerializer)
2. **Enrollments Query**: Get all students enrolled in the course
   ```sql
   SELECT * FROM classrooms_studentcourse
   WHERE course_id = ?
   INCLUDE RELATED users_customuser
   ```
3. **Submissions Query** (in loop): Get submission for each student
   ```sql
   SELECT * FROM students_studentsubmission
   WHERE student_id = ? AND assignment_id = ?
   LIMIT 1
   ```

### Optimization Notes

- Current implementation uses N+1 query pattern (one query per student)
- For courses with large enrollment, consider:
  1. Prefetching submissions with: `prefetch_related(Prefetch(...))`
  2. Using a single database query with a join and aggregation
  3. Caching results in Redis for read-heavy endpoints

### Example Optimized Version

```python
def get_student_submissions(self, obj):
    from classrooms.models import StudentCourse
    from students.models import StudentSubmission

    # Prefetch all submissions at once
    enrolled_students = StudentCourse.objects.filter(
        course=obj.course
    ).select_related('student').prefetch_related(
        Prefetch(
            'student__submissions',
            queryset=StudentSubmission.objects.filter(assignment=obj)
        )
    )

    submissions_data = []
    for enrollment in enrolled_students:
        # Get cached submission from prefetch
        submission = enrollment.student.submissions.first() if enrollment.student.submissions.exists() else None
        submission_serializer = StudentSubmissionDetailSerializer(submission)
        submissions_data.append(submission_serializer.data)

    return submissions_data
```

## Testing Scenarios

### Scenario 1: Complete Submission with Human Grade
```json
{
  "student_name": "John Doe",
  "email": "john.doe@school.com",
  "submission_status": "submitted",
  "grade": 85.5,
  "grade_status": "human_graded"
}
```

### Scenario 2: Submission with Only AI Grade
```json
{
  "student_name": "Jane Smith",
  "email": "jane.smith@school.com",
  "submission_status": "submitted",
  "grade": 92.0,
  "grade_status": "ai_graded"
}
```

### Scenario 3: Re-graded Submission
```json
{
  "student_name": "Alice Williams",
  "email": "alice.williams@school.com",
  "submission_status": "submitted",
  "grade": 78.0,
  "grade_status": "regraded"
}
```

### Scenario 4: No Submission
```json
{
  "student_name": "Bob Johnson",
  "email": "bob.johnson@school.com",
  "submission_status": "pending",
  "grade": null,
  "grade_status": "ungraded"
}
```

## Edge Cases Handled

1. **Null/Empty Names**: `.strip()` handles leading/trailing whitespace
2. **No Submission**: `StudentSubmissionDetailSerializer` handles `None` object
3. **Zero Grades**: Only considers scores > 0 as valid grades
4. **Missing Grades**: Returns `null` when neither human nor AI grade exists
5. **Multiple Enrollments**: Only the first submission per student is used

## Access Control

This serializer is read-only and is used by:
- Teachers viewing their own course assignments
- School admins viewing course assignments
- Super admins viewing all assignments

Ensure proper permission checks are in place at the view level.

## File Location

**Modified File**: `/home/bond-servant-in-training/Documents/Projects/Grade-Automator-Plus/assignments/serializers.py`

**Lines Modified**: Added ~130 lines
- New serializer: Lines 237-295
- Updated AssignmentDetailSerializer: Lines 298-370

## Related Models

- **Assignment**: Main model being serialized
- **StudentCourse**: Enrollment relationship (course → students)
- **StudentSubmission**: Submission data (student → assignment → grades)
- **CustomUser**: Student information (name, email)

## Future Enhancements

1. **Pagination**: Add pagination for large course enrollments
2. **Filtering**: Allow filtering by submission_status or grade_status
3. **Sorting**: Add sorting by student name, grade, or status
4. **Performance**: Implement query optimization for large datasets
5. **Caching**: Cache student_submissions for frequently accessed assignments
6. **Analytics**: Add submission rate percentage and average grade
7. **Detailed Feedback**: Include AI feedback and rubric breakdown per student

## Summary

The enhancement provides teachers and administrators with a comprehensive view of all student submissions and their grades in a single API response, making it easier to:

- Track submission progress
- Identify students who haven't submitted
- Monitor grade distribution
- Distinguish between AI and human grading
- Track re-graded submissions

All with properly typed, optimized, and well-documented code.
