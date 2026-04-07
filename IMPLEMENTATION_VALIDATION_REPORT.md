# Score Percentage and Max Points Implementation - Validation Report

**Date**: April 1, 2026
**Status**: ✅ MOSTLY CORRECT with ONE CRITICAL ISSUE to fix

---

## Executive Summary

You've successfully implemented `score_percentage` and `max_points` fields in the StudentSubmission model and are populating them in the grading logic. However, there is **ONE CRITICAL ISSUE** in the manual grade update view that needs fixing: the new fields are not being included in the `save()` operation.

---

## 1. ✅ Model Implementation - CORRECT

**File**: `/students/models.py` (Lines 39-54)

```python
score_percentage = models.DecimalField(
    max_digits=5,
    decimal_places=2,
    null=True,
    blank=True,
    help_text=_(
        "Score as a percentage of total possible points (calculated from score and assignment max_points)"
    ),
)

max_points = models.IntegerField(
    null=True,
    blank=True,
    help_text=_("Maximum points available for this assignment"),
)
```

**Assessment**: ✅ **CORRECT**
- Fields are properly defined with correct data types
- `score_percentage`: DecimalField with max_digits=5, decimal_places=2 (0-100.00 range)
- `max_points`: IntegerField for context
- Both have appropriate help text
- Both allow NULL values for ungraded submissions

---

## 2. ✅ AI Grading Logic - CORRECT

**File**: `/students/services.py` (Lines 121-135)

```python
grading_score = grading["grading_summary"]["total_score"]
max_points = grading["grading_summary"]["max_total_points"]
percentage = grading["grading_summary"]["percentage"]
grading_confidence = grading["grading_confidence"]

submission.score = grading_score
submission.ai_score = grading_score
submission.max_points = max_points
submission.score_percentage = percentage

submission.feedback = grading
submission.grading_confidence = grading_confidence
submission.graded_at = timezone.now()

submission.save()
```

**Assessment**: ✅ **CORRECT**
- Extracting all three values from AI grading response
- Assigning to model fields correctly
- `score` and `ai_score` get raw score
- `score_percentage` gets percentage
- `max_points` gets maximum points
- All fields saved in single `save()` call

---

## 3. ❌ Manual Grade Update - ISSUE FOUND

**File**: `/students/views.py` (Lines 700-735)

```python
score = serializer.validated_data["score"]
total_score = feedback["grading_summary"]["total_score"]
percentage = (score / total_score) * 100

feedback["grading_summary"]["total_score"] = score
feedback["grading_summary"]["percentage"] = percentage

submission.score = score
submission.score_percentage = percentage              # ← Assigned
submission.max_points = total_score                  # ← Assigned

submission.feedback = feedback
submission.was_regraded = True
submission.regraded_at = timezone.now()

# ... formatted_grade logic ...

submission.save(update_fields=["score", "feedback", "formatted_grade"])
#                            ↑ PROBLEM: Missing "score_percentage" and "max_points"
```

**Assessment**: ❌ **CRITICAL ISSUE**

**The Problem**:
```python
# These fields are assigned...
submission.score_percentage = percentage
submission.max_points = total_score

# But NOT saved because they're missing from update_fields list
submission.save(update_fields=["score", "feedback", "formatted_grade"])
```

**Impact**:
- When a teacher manually updates a grade, `score_percentage` and `max_points` are calculated and assigned
- BUT they are NOT saved to the database because they're not in the `update_fields` list
- The database record will have NULL values for these fields
- This breaks the final grade calculation logic that depends on `score_percentage`

**How Django's save() works**:
```python
# When update_fields is specified, ONLY those fields are updated
submission.save(update_fields=["score", "feedback", "formatted_grade"])
# Result: Only score, feedback, formatted_grade are updated in DB
# Result: score_percentage and max_points remain NULL

# To fix, need to include them:
submission.save(update_fields=["score", "score_percentage", "max_points", "feedback", "formatted_grade"])
```

---

## 4. ✅ StudentSubmissionListSerializer - CORRECT

**File**: `/students/serializers.py` (Lines 113-139)

```python
class StudentSubmissionListSerializer(serializers.ModelSerializer):
    student_name = serializers.SerializerMethodField()
    assignment_title = serializers.CharField(source="assignment.title", read_only=True)
    course = serializers.CharField(source="assignment.course.id", read_only=True)

    class Meta:
        model = StudentSubmission
        fields = [
            "id",
            "student",
            "student_name",
            "assignment",
            "assignment_title",
            "course",
            "submission_date",
            "score",
            "score_percentage",        # ✅ Included
            "max_points",              # ✅ Included
            "graded_at",
        ]

        read_only_fields = [
            "submission_date",
            "student_name",
            "assignment_title",
            "course",
            "score",
            "score_percentage",        # ✅ Read-only
            "max_points",              # ✅ Read-only
            "graded_at",
        ]
```

**Assessment**: ✅ **CORRECT**
- Fields are included in the serializer
- Fields are marked as read-only (correct, since they're auto-calculated)
- Will return these fields in API responses

---

## 5. ⚠️ AssignmentDetailSerializer - INCOMPLETE

**File**: `/assignments/serializers.py` (Lines 302-347)

**Current Implementation**:
```python
def get_student_submissions(self, obj):
    # ... fetches enrollments and submissions ...

    # Grade — prefer human score, fall back to AI score
    grade = None
    if submission:
        if submission.score is not None:
            grade = float(submission.score)
        elif submission.ai_score is not None:
            grade = float(submission.ai_score)

    # ... builds result dict ...
    result.append({
        "name": student.get_full_name(),
        "email": student.email,
        "submission_status": submission_status,
        "grade": grade,
        "grade_status": grade_status,
    })
```

**Assessment**: ⚠️ **SHOULD USE PERCENTAGE**

**Current Logic**:
- Returns `grade` as raw score (e.g., 86)
- Useful but not normalized across assignments

**Recommended Change**:
```python
# Should prefer score_percentage instead
grade = None
if submission:
    if submission.score_percentage is not None:
        grade = float(submission.score_percentage)
    elif submission.score is not None:
        grade = float(submission.score)
    elif submission.ai_score is not None:
        grade = float(submission.ai_score)

# Better yet, add both fields to response
result.append({
    "name": student.get_full_name(),
    "email": student.email,
    "submission_status": submission_status,
    "grade": grade,
    "grade_percentage": score_percentage,      # ADD THIS
    "max_points": submission.max_points,       # ADD THIS
    "grade_status": grade_status,
})
```

---

## 6. Missing: Signal Handler for Auto-Calculation

**Status**: ❌ **NOT IMPLEMENTED YET**

The SCORING_ANALYSIS.md document recommends a signal handler to automatically calculate `StudentCourse.final_grade` when submissions change:

```python
# Should exist in classrooms/signals.py but NOT FOUND
@receiver(post_save, sender=StudentSubmission)
def update_student_course_final_grade(sender, instance, **kwargs):
    # Calculate final_grade from average of score_percentage
    pass
```

---

## 7. Summary Table

| Component | Status | Issue | Fix Required |
|-----------|--------|-------|--------------|
| **Model Fields** | ✅ | None | No |
| **AI Grading** | ✅ | None | No |
| **Manual Grade Update** | ❌ | Missing fields in save() | **YES - CRITICAL** |
| **Serializers** | ⚠️ | Should return percentage | Recommended |
| **Signal Handler** | ❌ | Not implemented | Future |
| **Documentation** | ✅ | None | No |

---

## 8. FIXES REQUIRED

### Fix #1: Update Manual Grade Update View (CRITICAL)

**File**: `/students/views.py` (Line 735)

**Current**:
```python
submission.save(update_fields=["score", "feedback", "formatted_grade"])
```

**Fixed**:
```python
submission.save(update_fields=["score", "score_percentage", "max_points", "feedback", "formatted_grade", "was_regraded", "regraded_at"])
```

**Why**: The new fields need to be in the update_fields list to actually save to the database.

---

### Fix #2 (Recommended): Update AssignmentDetailSerializer

**File**: `/assignments/serializers.py` (Lines 324-347)

**Current**:
```python
grade = None
if submission:
    if submission.score is not None:
        grade = float(submission.score)
    elif submission.ai_score is not None:
        grade = float(submission.ai_score)

result.append({
    "name": student.get_full_name(),
    "email": student.email,
    "submission_status": submission_status,
    "grade": grade,
    "grade_status": grade_status,
})
```

**Recommended**:
```python
grade = None
grade_percentage = None
if submission:
    # Prefer percentage if available
    if submission.score_percentage is not None:
        grade_percentage = float(submission.score_percentage)

    # Prefer human score, then AI score
    if submission.score is not None:
        grade = float(submission.score)
    elif submission.ai_score is not None:
        grade = float(submission.ai_score)

result.append({
    "name": student.get_full_name(),
    "email": student.email,
    "submission_status": submission_status,
    "grade": grade,
    "grade_percentage": grade_percentage,
    "max_points": submission.max_points,
    "grade_status": grade_status,
})
```

**Why**: Provides complete grading information - raw score AND percentage. Teachers can see "85/100 (85%)" instead of just "85".

---

## 9. Testing Checklist

After applying fixes, test the following:

### Test 1: AI Grading
- [ ] Grade an assignment with AI
- [ ] Verify `score`, `score_percentage`, and `max_points` are all saved
- [ ] Check assignment detail endpoint returns student_submissions correctly

### Test 2: Manual Grade Update
- [ ] Manually update a grade via `/submissions/{id}/update-grade/`
- [ ] Verify ALL fields are saved: score, score_percentage, max_points, feedback
- [ ] Check StudentSubmissionListSerializer returns percentage

### Test 3: Dashboard Endpoints
- [ ] Call assignment detail endpoint
- [ ] Verify student_submissions include percentage
- [ ] Calculate course final grade using average of percentages

### Test 4: Data Consistency
- [ ] For all graded submissions, verify:
  - `score + max_points` = valid ratio (score ≤ max_points)
  - `score_percentage` = (score / max_points) * 100 (or 0-100 range)

---

## 10. Migration Status

**Assumption**: You have already run:
```bash
python manage.py makemigrations students
python manage.py migrate
```

**To verify**:
```bash
# Check if migration exists
ls students/migrations/ | grep score_percentage

# Check if fields are in DB
python manage.py dbshell
# SELECT score_percentage, max_points FROM students_studentsubmission LIMIT 1;
```

---

## 11. Recommendations

### Immediate (Must Do)
1. **Fix the save() call** in `/students/views.py` to include new fields

### Short-term (Should Do)
2. **Update AssignmentDetailSerializer** to return score_percentage
3. **Write tests** for manual grade update to verify all fields save

### Medium-term (Nice to Have)
4. **Implement signal handler** in `/classrooms/signals.py` for automatic final_grade calculation
5. **Add database index** on `score_percentage` for faster queries
6. **Backfill percentages** for existing submissions

---

## Conclusion

**Overall Grade: B+ (Good Implementation with One Critical Issue)**

✅ **What you did right**:
- Model design is correct
- AI grading logic properly saves all fields
- Serializers include new fields
- Good help text and documentation

❌ **What needs fixing**:
- Manual grade update doesn't save the new fields (CRITICAL)

⚠️ **What could be improved**:
- AssignmentDetailSerializer should return percentage for complete information
- Signal handler for auto-calculation not yet implemented

**Estimated time to fix**: 10-15 minutes (Fix #1 is a one-liner change)

Would you like me to apply the fixes?
