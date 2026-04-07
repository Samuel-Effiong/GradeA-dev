# Assignment Visibility & Student Access Analysis

**Date**: April 2, 2026
**Question**: Can students see assignments that were uploaded by teacher but not published?

**Answer**: ✅ **NO - Students CANNOT see unpublished assignments**

---

## How Visibility Works

### Assignment Status Model

**File**: `/assignments/models.py` (Lines 19-21)

```python
class AssignmentStatus(models.TextChoices):
    DRAFT = "DRAFT", _("DRAFT")
    PUBLISHED = "PUBLISHED", _("PUBLISHED")
```

Assignments have two states:
- **DRAFT**: Created but not ready for students
- **PUBLISHED**: Ready for students to see and submit

---

## Student Assignment Access Control

### The Permission Filter

**File**: `/assignments/views.py` (Lines 181-191)

```python
def get_queryset(self):
    user = self.request.user

    if user.user_type == UserTypes.TEACHER:
        # Teachers see all their assignments
        return Assignment.objects.filter(course__teacher=user)

    elif user.user_type == UserTypes.STUDENT:
        # Students see ONLY PUBLISHED assignments in their enrolled courses
        return Assignment.objects.filter(
            course__enrollments__student=user,           # ← Must be enrolled
            status=AssignmentStatus.PUBLISHED            # ← Must be PUBLISHED
        )
    else:
        return Assignment.objects.none()
```

### What This Means

```
Student query: "Show me assignments"
    ↓
System checks TWO conditions:
├─ Is student enrolled in the course? AND
└─ Is assignment status = PUBLISHED?

Result:
✅ If BOTH are true → Assignment visible to student
❌ If either is false → Assignment NOT visible to student
```

---

## Student Access Scenarios

### Scenario 1: DRAFT Assignment (Not Published)
```
Assignment details:
├─ Status: DRAFT
├─ Course: Math 101
└─ Teacher: Mr. Smith

Student enrolled in Math 101?
    ↓
Query: status=PUBLISHED
    ↓
Result: ❌ DRAFT ≠ PUBLISHED
    ↓
Student sees: NOTHING
```

### Scenario 2: PUBLISHED Assignment
```
Assignment details:
├─ Status: PUBLISHED
├─ Course: Math 101
└─ Teacher: Mr. Smith

Student enrolled in Math 101?
    ↓
Query: status=PUBLISHED
    ↓
Result: ✅ PUBLISHED = PUBLISHED
    ↓
Student sees: Assignment available to submit
```

### Scenario 3: PUBLISHED but Not Enrolled
```
Assignment details:
├─ Status: PUBLISHED
├─ Course: Math 101
└─ Teacher: Mr. Smith

Student enrolled in Math 101?
    ↓
Query: course__enrollments__student=user
    ↓
Result: ❌ Student not enrolled
    ↓
Student sees: NOTHING
```

---

## Data Flow for Assignment Visibility

```
┌─────────────────────────────────────────────────────────────┐
│ Student requests: GET /api/v1/assignments/                  │
└────────────────┬────────────────────────────────────────────┘
                 │
                 ↓
        ┌─────────────────┐
        │ Check user type │
        └────────┬────────┘
                 │
        ┌────────┴────────┐
        ↓                 ↓
    TEACHER          STUDENT
        │                 │
        ↓                 ↓
   Return all      Apply TWO filters:
   their courses   ├─ course__enrollments__student = user
                   └─ status = "PUBLISHED"
                        │
                        ↓
                   Return matching
                   assignments only
```

---

## Practical Example

**Course**: Physics 101 (5 assignments)

```
Assignment 1: DRAFT     → Student sees: ❌ NOTHING
Assignment 2: PUBLISHED → Student sees: ✅ Available
Assignment 3: DRAFT     → Student sees: ❌ NOTHING
Assignment 4: PUBLISHED → Student sees: ✅ Available
Assignment 5: PUBLISHED → Student sees: ✅ Available (if enrolled)
```

**Student View in API Response**:
```json
{
  "count": 3,
  "results": [
    {
      "id": "uuid-2",
      "title": "Forces and Motion",
      "status": "PUBLISHED",
      ...
    },
    {
      "id": "uuid-4",
      "title": "Energy Conservation",
      "status": "PUBLISHED",
      ...
    },
    {
      "id": "uuid-5",
      "title": "Waves",
      "status": "PUBLISHED",
      ...
    }
  ]
}
```

Student CANNOT see assignments 1 and 3 (DRAFT status).

---

## Teacher View vs Student View

### What Teacher Sees (All assignments they created)
```
GET /api/v1/assignments/

Returns:
├─ Assignment 1 (DRAFT) ✓
├─ Assignment 2 (PUBLISHED) ✓
├─ Assignment 3 (DRAFT) ✓
├─ Assignment 4 (PUBLISHED) ✓
└─ Assignment 5 (PUBLISHED) ✓

Total: 5 assignments (all statuses)
```

### What Student Sees (Only published)
```
GET /api/v1/assignments/

Returns:
├─ Assignment 2 (PUBLISHED) ✓
├─ Assignment 4 (PUBLISHED) ✓
└─ Assignment 5 (PUBLISHED) ✓

Total: 3 assignments (PUBLISHED only)
```

---

## How to Publish an Assignment

### Step 1: Teacher Creates Assignment (DRAFT)
```
POST /api/v1/assignments/
{
  "title": "Test Quiz",
  "course": "uuid-course",
  "status": "DRAFT"  ← Default status
}

Result: Assignment created but INVISIBLE to students
```

### Step 2: Teacher Publishes Assignment
```
PATCH /api/v1/assignments/{id}/
{
  "status": "PUBLISHED"  ← Update status
}

Result: Assignment now VISIBLE to all enrolled students
```

---

## Database Query (Behind the Scenes)

When a student requests their assignments, Django executes:

```sql
SELECT * FROM assignments_assignment
WHERE
  course_id IN (
    SELECT course_id FROM classrooms_studentcourse
    WHERE student_id = <student_user_id>
  )
  AND status = 'PUBLISHED'
```

Translation:
> "Show me all PUBLISHED assignments from courses I'm enrolled in"

---

## Security Implications

### ✅ This is Secure Because:

1. **Draft Protection**
   - Teachers can prepare assignments without students seeing
   - Time to set up grading rubrics, test content, etc.

2. **Enrollment Verification**
   - Students can't see assignments from courses they're not in
   - Even if assignment is PUBLISHED

3. **Permission-Based Access**
   - Done at database query level (not just UI hiding)
   - Students literally cannot fetch draft assignments via API

---

## Summary Table

| Scenario | Student Enrolled | Status | Visible to Student |
|----------|------------------|--------|-------------------|
| Physics 101, Assn 1 | ✓ Yes | DRAFT | ❌ NO |
| Physics 101, Assn 2 | ✓ Yes | PUBLISHED | ✅ YES |
| Physics 101, Assn 3 | ✗ No | PUBLISHED | ❌ NO |
| Math 101, Assn 4 | ✓ Yes | DRAFT | ❌ NO |
| Math 101, Assn 5 | ✓ Yes | PUBLISHED | ✅ YES |

---

## Answer to Your Question

### "Can student see assignments that was uploaded by teacher but not published?"

**Answer: NO ❌**

Students can **ONLY** see assignments that meet **BOTH** conditions:
1. ✅ The assignment status is **PUBLISHED**
2. ✅ The student is **ENROLLED** in the course

If either condition is not met, the assignment is completely hidden from the student's view.

This is enforced at the **database query level** in the `get_queryset()` method, so students cannot bypass this restriction even if they try to manipulate the API.

---

## Implementation Details

**Files involved in visibility control**:
- `/assignments/models.py` - AssignmentStatus choices
- `/assignments/views.py` - AssignmentViewSet.get_queryset() method
- `/classrooms/models.py` - StudentCourse enrollment tracking

**Query logic** (line 187-189 in views.py):
```python
return Assignment.objects.filter(
    course__enrollments__student=user,     # Student must be enrolled
    status=AssignmentStatus.PUBLISHED      # Assignment must be published
)
```

This is a clean, database-enforced permission model that's both secure and performant.
