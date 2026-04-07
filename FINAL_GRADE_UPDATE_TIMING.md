# Final Grade Update Timing Analysis

**Date**: April 1, 2026
**Question**: Is the final grade for a student updated when a new submission is made?

**Answer**: ⚠️ **NO - There's a gap in your implementation**

---

## Current Signal Implementation

**File**: `/classrooms/signals.py` (Lines 90-113)

```python
@receiver(post_save, sender=StudentSubmission)
def update_student_course_final_grade(sender, instance, **kwargs):
    """
    When a submission is graded, recalculate the student's final grade
    using the average of all submission percentages.
    """

    # ... code ...

    avg_percentage = StudentSubmission.objects.filter(
        student=student,
        assignment__course=course,
        score_percentage__isnull=False  # ← KEY CONDITION
    ).aggregate(Avg("score_percentage"))["score_percentage__avg"]
```

---

## The Problem: Signal Only Updates on Graded Submissions

### The Issue

The signal filters for:
```python
score_percentage__isnull=False  # Only graded submissions
```

This means **final_grade is ONLY updated when a submission has a grade** (score_percentage is not NULL).

---

## Submission Lifecycle

Let's trace what happens when a student submits an assignment:

### Step 1: Student Uploads Assignment
**File**: `/students/views.py` (Line 341: upload_answers)

```
Student submits assignment
    ↓
upload_answers_engine() called
    ↓
StudentSubmission.objects.get_or_create(...)  # CREATED
    ↓
submission.answers = <student answers>
submission.raw_input = <html>
submission.save()  # ← Saves to database
```

**At this point**:
```
StudentSubmission state:
├─ id: UUID
├─ student: John
├─ assignment: Math Quiz
├─ answers: {...}
├─ raw_input: {...}
├─ score: 0.00 (default) or NULL
├─ score_percentage: NULL ← NOT GRADED
├─ max_points: NULL
└─ submission_date: 2026-04-01 10:00

Signal fires post_save...
    ↓
Checks: score_percentage__isnull=False
    ↓
Result: NO submissions match (none graded yet)
    ↓
final_grade NOT UPDATED ← PROBLEM!
```

### Step 2: AI Grades the Submission
**File**: `/students/services.py` (Line 121: grade_assignment_engine)

```python
grading_score = grading["grading_summary"]["total_score"]      # 85
max_points = grading["grading_summary"]["max_total_points"]    # 100
percentage = grading["grading_summary"]["percentage"]          # 85.00

submission.score = grading_score
submission.ai_score = grading_score
submission.max_points = max_points
submission.score_percentage = percentage
submission.graded_at = timezone.now()
submission.save()  # ← NOW IT'S GRADED
```

**At this point**:
```
StudentSubmission state:
├─ score: 85.00
├─ score_percentage: 85.00 ← NOW SET
└─ max_points: 100

Signal fires post_save...
    ↓
Checks: score_percentage__isnull=False
    ↓
Result: YES! This submission is graded
    ↓
Calculates avg_percentage from all graded submissions
    ↓
Updates StudentCourse.final_grade ✅ WORKS HERE
```

---

## Timeline: When Does Final Grade Update?

```
Timeline                                              Final Grade Status
────────────────────────────────────────────────────────────────────────

10:00 AM - Student submits assignment 1              ❌ NOT UPDATED
10:05 AM - Student submits assignment 2              ❌ NOT UPDATED
10:10 AM - AI grades assignment 1 (85%)              ✅ UPDATED (avg = 85%)
10:15 AM - AI grades assignment 2 (75%)              ✅ UPDATED (avg = 80%)
10:20 AM - Teacher manually grades assignment 1 (90%)✅ UPDATED (avg = 82.5%)
```

---

## Current Behavior vs Desired Behavior

### Current Behavior (Gap Found)

```
┌─────────────────────────────────────┐
│ Student Submits New Assignment      │
│ (score_percentage = NULL)           │
└────────────────┬────────────────────┘
                 │
                 ↓
         Signal Fires
                 │
                 ↓
    Filter: score_percentage__isnull=False
                 │
                 ↓
         No results (not graded yet)
                 │
                 ↓
      ❌ final_grade NOT UPDATED
```

### Desired Behavior (What You Might Want)

#### Option A: Only Update on Grade (Current)
- ✅ final_grade reflects graded work only
- ✅ Ungraded submissions don't affect grade
- ❌ Misleading: Final grade doesn't account for all assignments

#### Option B: Update on Any Change (Recommended)
- ✅ final_grade always represents all submissions
- ✅ Shows progress including ungraded work
- ✅ Accounts for submission count and status
- ❌ Slightly more processing

---

## What Should Happen?

### Analysis of Your System

Your system uses **Option A** (implicit):
> "Only update final_grade when submissions are graded"

**This means**:
- ✅ Unsubmitted assignments don't affect grade
- ✅ Partially graded courses show accurate grade
- ❌ Final grade jumps when new assignments are graded
- ❌ A student might be missing assignment 3, but final_grade doesn't reflect this

**Example Problem**:
```
Course has 4 assignments worth 25 points each (100 total)

Student state:
├─ Assignment 1: Submitted ✓, Graded ✓ (85%)
├─ Assignment 2: Submitted ✓, Graded ✓ (75%)
├─ Assignment 3: Submitted ✓, NOT Graded ✗
└─ Assignment 4: NOT Submitted ✗

Current final_grade = (85 + 75) / 2 = 80%

But student is missing:
- Assignment 3 (might be 0%)
- Assignment 4 (might be 0%)

Realistic final_grade should be: (85 + 75 + 0 + 0) / 4 = 40%
```

---

## Two Solutions

### Solution 1: Current Approach (Keep As-Is)

**Signal fires only when grade is added**:
```python
# Current code - no change needed
avg_percentage = StudentSubmission.objects.filter(
    student=student,
    assignment__course=course,
    score_percentage__isnull=False  # Only graded
).aggregate(Avg("score_percentage"))["score_percentage__avg"]
```

**Pros**:
- ✅ Only grades work that's been evaluated
- ✅ Partial grades make sense
- ✅ No impact from unsubmitted work

**Cons**:
- ❌ Doesn't show true course progress
- ❌ Final grade incomplete until all graded
- ❌ Misleading for partially graded courses

---

### Solution 2: Update on Any Submission Event (Recommended)

**Modify signal to fire on any submission state change**:

```python
@receiver(post_save, sender=StudentSubmission)
def update_student_course_final_grade(sender, instance, **kwargs):
    """
    When a submission is created, updated, or graded, recalculate the student's
    final grade including both graded and ungraded submissions.
    """

    student = instance.student
    course = instance.assignment.course

    try:
        enrollment = StudentCourse.objects.get(student=student, course=course)
    except StudentCourse.DoesNotExist:
        return

    # Method A: Average only graded submissions (current)
    # avg_percentage = StudentSubmission.objects.filter(
    #     student=student,
    #     assignment__course=course,
    #     score_percentage__isnull=False
    # ).aggregate(Avg("score_percentage"))["score_percentage__avg"]

    # Method B: Average all submissions (ungraded = 0%)
    submissions = StudentSubmission.objects.filter(
        student=student,
        assignment__course=course
    )

    if not submissions.exists():
        return

    total_percentage = 0
    for submission in submissions:
        # Use percentage if graded, else 0
        total_percentage += submission.score_percentage or 0

    avg_percentage = total_percentage / submissions.count()

    enrollment.final_grade = avg_percentage
    enrollment.save(update_fields=["final_grade"])
```

**This method treats ungraded as 0%**:
```
Assignment 1: Graded 85%
Assignment 2: Graded 75%
Assignment 3: Ungraded (0%)
Assignment 4: Ungraded (0%)

Final grade = (85 + 75 + 0 + 0) / 4 = 40%
```

---

### Solution 3: Calculate with Assignment Weights

If assignments have different weights:

```python
@receiver(post_save, sender=StudentSubmission)
def update_student_course_final_grade(sender, instance, **kwargs):

    student = instance.student
    course = instance.assignment.course

    try:
        enrollment = StudentCourse.objects.get(student=student, course=course)
    except StudentCourse.DoesNotExist:
        return

    # Get all assignments for the course
    assignments = Assignment.objects.filter(course=course)
    total_weight = assignments.count()

    if total_weight == 0:
        return

    weighted_score = 0
    for assignment in assignments:
        submission = StudentSubmission.objects.filter(
            student=student,
            assignment=assignment
        ).first()

        if submission and submission.score_percentage:
            weighted_score += submission.score_percentage
        # else: ungraded = 0 points

    final_grade = weighted_score / total_weight
    enrollment.final_grade = final_grade
    enrollment.save(update_fields=["final_grade"])
```

---

## Recommendation for Your System

### Based on Your Current Implementation

You're using **Solution 1** (grade-only):
- final_grade updates ONLY when submissions are graded
- Doesn't account for unsubmitted/ungraded work

### What I Recommend

**Keep Solution 1 IF**:
- You want to show only evaluated work
- Ungraded assignments shouldn't penalize students
- Teachers grade assignments quickly

**Switch to Solution 2 IF**:
- You want realistic course progress
- You want to show missing assignments impact
- You want final_grade to always reflect all work

**My Recommendation**: Switch to **Solution 2** because:
- ✅ More honest representation of student progress
- ✅ Teachers can see "Missing 1 of 4 assignments"
- ✅ Final grade accounts for all work, not just graded
- ✅ Alignment with traditional grading (missing = 0)

---

## Answer to Your Question

### "Is final_grade updated when a new submission is made?"

**Current Answer**: ❌ **NO**

**What actually happens**:
1. Student submits assignment → signal fires → **final_grade NOT updated** (no grade yet)
2. Teacher grades assignment → signal fires → **final_grade IS updated** (grade exists)

**Timeline**:
```
Student submits 3 assignments (ungraded)
    → final_grade unchanged

Teacher grades assignment 1 (85%)
    → final_grade = 85%

Teacher grades assignment 2 (75%)
    → final_grade = 80%

Student submits assignment 4
    → final_grade still = 80% (new submission not graded)

Teacher grades assignment 3 (90%)
    → final_grade = 83.33%
```

---

## What You Should Do

### Option 1: Keep Current (Simplest)
- No changes needed
- Document this behavior
- Final grade only reflects graded work

### Option 2: Modify Signal (Recommended)
- Update signal to include ungraded submissions as 0%
- More realistic final grade
- Better represents student progress

**Which would you prefer?**

I can help implement Option 2 if you'd like to update the signal to include ungraded assignments in the final grade calculation.
