# LicenseSubscriptionService - Code Review & Bug Analysis

## Original review: 2026-06-09
## Revised against implementation: 2026-09-03 (section 2 audit)

> **Read this first.** The original review below was written on
> 2026-06-09 and had drifted from the code. It was re-verified line by
> line during the section 2 pass of `docs/CODEBASE_AUDIT_SECTIONS.md`;
> the entries marked **[UPDATED 2026-09-03]** are the ones that had gone
> stale. Where this document and the code disagree, the code is correct
> — check it before relying on anything here.

### Summary of the drift found

| Original claim | Reality on 2026-09-03 |
|---|---|
| `validate_admin_user` "allows TEACHER or SCHOOL_ADMIN"; being stricter is an *optional* enhancement | **Hardened.** It now also rejects `SUPER_ADMIN`/`is_superuser` and *requires* school membership. This closed a real privilege-escalation path, not a nicety. |
| Recommendation #4: "Create scheduled tasks for `process_license_renewal`" | **Done.** `billing.tasks.process_license_renewals` runs daily via `CELERY_BEAT_SCHEDULE` and is watched by `BEAT_HEALTH_EXPECTATIONS`. |
| "Immutable ledger (GRANT entries can't be modified)" | True **now**, but it was aspirational when written — nothing enforced it until `billing/immutable.py` (`e2dc83b`). A `CASCADE` was deleting ~17.8k ledger rows with a single user. |
| Issue #1: `stripe_subscription_id` unset at licence creation | **Still accurate**, and still benign — the field is nullable and the cancellation paths branch on it. |

---

## IMPLEMENTATION OVERVIEW

### Architecture
- **Isolation**: Completely separate from `SubscriptionService` (Individual subscriptions)
- **Pattern**: All methods are `@staticmethod` with `@transaction.atomic` for ACID compliance
- **Logging**: Comprehensive logging at all key checkpoints
- **Error Handling**: Defensive validation and graceful error handling

---

## CORE METHODS ANALYSIS

### 1. `validate_license_plan(plan)`
**Purpose**: Ensure plan is suitable for License subscriptions
**Status**: ✅ CORRECT

**Validation checks**:
- ✅ Category must be LICENSE
- ✅ monthly_credits must not be None or 0

**Potential Issues**: NONE
- Correctly rejects INDIVIDUAL plans
- Correctly rejects CUSTOM/contact-sales plans

---

### 2. `validate_admin_user(admin_user, school)`
**Purpose**: Ensure admin has authorization to manage licenses
**Status**: ✅ CORRECT — **[UPDATED 2026-09-03]**

**Validation checks (current code):**
- ✅ Rejects STUDENT users
- ✅ Rejects `SUPER_ADMIN` / `is_superuser`
- ✅ Requires `admin_user.school_id` to be set
- ✅ Requires it to match the licence's school

**What changed since the original review.** The original entry called
stricter checking an "optional enhancement" and judged the loose version
"acceptable (allows flexibility)". That was wrong, and the code no longer
matches it.

`admin_user` is not bookkeeping: it decides who receives the school's
admin credit allocation, whose email is the billing contact, and who may
request/approve overage. The old check only rejected an `admin_user`
whose school was set **and different**, so anyone with `school=None`
passed — and a `SUPER_ADMIN` is exactly that. A superadmin could name
themselves a school's licence admin, divert that school's admin credit
allocation into their own wallet, and (via
`IsSchoolAdminOrSuperAdmin.has_object_permission`) displace the school's
real admin.

Membership is now required rather than merely "not contradicted". The
mirror-image invariant is enforced on the identity side by
`users.serializers.CustomUserSerializer.validate`, and covered by
`billing/tests/test_license_admin_user_guard.py` and
`users/tests_superadmin_tenancy.py`.

---

### 3. `create_license_subscription(school, plan, admin_user, teacher_ids=None)`
**Purpose**: Create a school-level license with optional teacher enrollment
**Status**: ✅ CORRECT

**Key Operations**:
1. ✅ Validates plan and admin
2. ✅ Deactivates existing active license (one per school)
3. ✅ Creates LicenseSubscription object
4. ✅ Enrolls teachers if IDs provided
5. ✅ Graceful error handling for invalid teacher IDs

**Strengths**:
- Single school-wide license constraint enforced
- Atomic transaction ensures consistency
- Detailed logging for audit trail

**Verified Correctness**:
- Billing cycle set to 1 month: `billing_end = now + relativedelta(months=1)` ✅
- auto_renew defaults to True ✅
- Teacher enrollment errors don't fail entire operation ✅

---

### 4. `_enroll_teacher_internal(license_sub, teacher)`
**Purpose**: Internal method to enroll single teacher with comprehensive setup
**Status**: ✅ CORRECT

**Key Operations**:
1. ✅ Check for existing active allocation
2. ✅ Create/reactivate SchoolCreditAllocation
3. ✅ Ensure CreditWallet exists
4. ✅ Deactivate conflicting INDIVIDUAL subscriptions
5. ✅ Handle existing MONTHLY bucket rollover
6. ✅ Create new MONTHLY bucket
7. ✅ Create audit ledger entry
8. ✅ Reset overage blocks

**Verified Correctness**:

**Credit Allocation**:
- `allocation.monthly_allocation = license_sub.plan.monthly_credits` ✅
- Each teacher gets INDEPENDENT credit wallet ✅
- MONTHLY bucket expiry set to `license_sub.billing_cycle_end` ✅

**Rollover Logic**:
```python
rollover_amount = min(
    int(unused * (license_sub.plan.carry_over_percent / 100)),
    license_sub.plan.carry_over_max,
)
```
✅ CORRECT: Applies percentage cap and hard max

**Previous Subscription Handling**:
- ✅ Deactivates INDIVIDUAL subscriptions
- ✅ Carries over unused credits when transitioning

**Overage Reset**:
- ✅ Sets `overage_blocks_used = 0` (fresh start for license teachers)

---

### 5. `add_teacher_to_license(license_sub, teacher)`
**Purpose**: Add single teacher to existing license
**Status**: ✅ CORRECT

**Validation**:
- ✅ Checks if license is active (raises ValueError if not)

**Implementation**:
- ✅ Delegates to `_enroll_teacher_internal` (no code duplication)

---

### 6. `add_teachers_batch(license_sub, teacher_ids)`
**Purpose**: Add multiple teachers in single transaction
**Status**: ✅ CORRECT

**Batch Processing**:
- ✅ Atomic transaction for all teachers
- ✅ Tracks successful/failed counts
- ✅ Captures detailed error messages
- ✅ Continues on error (doesn't fail entire batch)

**Verified**:
```python
results = {
    "successful": 0,
    "failed": 0,
    "errors": []
}
```
✅ Provides complete feedback

---

### 7. `remove_teacher_from_license(license_sub, teacher)`
**Purpose**: Remove teacher from license (soft delete)
**Status**: ✅ CORRECT

**Rationale**:
- ✅ Sets `is_active=False` (preserves audit trail)
- ✅ Doesn't delete record (important for historical data)
- ✅ Raises ValueError if allocation doesn't exist (defensive)

---

### 8. `process_license_renewal(license_sub)`
**Purpose**: Handle monthly renewal for entire license with all enrolled teachers
**Status**: ✅ CORRECT - CRITICAL OPERATION

**Pre-Renewal Checks**:
- ✅ Verifies billing_cycle_end has passed
- ✅ Verifies license is still active
- ✅ Checks auto_renew flag (deactivates if False)

**Per-Teacher Renewal Logic**:
For each active teacher allocation:

1. **Get Old MONTHLY Bucket**:
   - ✅ Filters for `expires_at__lte=now`
   - ✅ Should only find one (or none if already renewed)

2. **Apply Rollover**:
   ```python
   rollover_amount = min(
       int(unused * (license_sub.plan.carry_over_percent / 100)),
       license_sub.plan.carry_over_max,
   )
   ```
   ✅ CORRECT: Same logic as enrollment

3. **Expire Old Bucket**:
   - ✅ Sets `expires_at = renewal_start`
   - ✅ Prevents double-counting of credits

4. **Create New MONTHLY Bucket**:
   - ✅ Fresh allocation with full credits
   - ✅ Expires at `renewal_end`
   - ✅ Creates audit ledger entry

5. **Reset Overage**:
   - ✅ `overage_blocks_used = 0`

6. **Update License Dates**:
   - ✅ Sets new billing cycle dates
   - ✅ Ensures cycle tracking stays accurate

**Verified**:
- ✅ Atomic transaction (all teachers or none)
- ✅ Comprehensive error handling with logging
- ✅ Each teacher processed independently
- ✅ Cycle dates updated after successful renewal

**CRITICAL VALIDATION**:
```python
if license_sub.billing_cycle_end > now:
    raise ValueError(...)
```
✅ CORRECT: Prevents early renewal

---

### 9. `update_license_plan(license_sub, new_plan)`
**Purpose**: Change license to new plan
**Status**: ✅ CORRECT

**Operations**:
- ✅ Validates new plan
- ✅ Updates reference
- ✅ Existing teachers keep current allocation until next renewal
- ✅ New teachers will get new allocation

**Note**: Does NOT immediately update teacher allocations
- ✅ CORRECT: Fairness (teachers keep current allocation in cycle)
- Updates will apply on next renewal

---

### 10. `cancel_license_subscription(license_sub)`
**Purpose**: Cancel a license subscription
**Status**: ✅ CORRECT

**Operations**:
- ✅ Sets `is_active=False`
- ✅ Sets `auto_renew=False`
- ✅ Teachers keep credits until cycle end
- ✅ Will not auto-renew

---

### 11. `get_teacher_allocation_info(teacher)`
**Purpose**: Get human-readable allocation info for teacher
**Status**: ✅ CORRECT

**Returns**:
- ✅ Complete info dict if teacher under license
- ✅ None if teacher not under license
- ✅ Includes both raw and display values (credits × 1000)

**Verified**:
```python
monthly_allocation_display = allocation.display_monthly_allocation  # Divides by 1000
current_balance_display = wallet.display_balance  # Uses math.floor for safety
```
✅ CORRECT: Proper unit conversions

---

## BUG ANALYSIS

### Potential Issues Found: 0 CRITICAL, 1 MINOR

---

### Issue #1: MINOR - Missing Default for Stripe Subscription ID
**Location**: `create_license_subscription()`
**Severity**: MINOR
**Details**:
- `LicenseSubscription.stripe_subscription_id` is not set during creation
- It's left as None/empty
- This is only problematic when processing payments

**Impact**:
- ⚠️ If Stripe integration calls code that expects this field populated, it will fail
- ✅ No impact for credit allocation logic

**Recommendation**:
- Either: Set stripe_subscription_id during creation (requires Stripe API call)
- Or: Mark as optional in your implementation and populate during payment processing
- **Current Status**: Code is safe as-is (field is nullable)

---

## TRANSACTION SAFETY ANALYSIS

### ✅ ALL OPERATIONS ARE ATOMIC

**Critical Operations Protected**:
- ✅ `create_license_subscription` - `@transaction.atomic`
- ✅ `_enroll_teacher_internal` - Called within atomic context
- ✅ `add_teachers_batch` - `@transaction.atomic`
- ✅ `remove_teacher_from_license` - `@transaction.atomic`
- ✅ `process_license_renewal` - `@transaction.atomic`
- ✅ `update_license_plan` - `@transaction.atomic`

**Race Condition Prevention**:
- ✅ `select_for_update()` used where appropriate
- ✅ Database constraints on unique fields
- ✅ Transaction atomicity prevents partial updates

---

## CREDIT ALLOCATION CORRECTNESS

### ✅ EACH TEACHER GETS INDEPENDENT CREDITS

**Verified**:
1. Each teacher has own `CreditWallet` (OneToOneField) ✅
2. Each teacher's wallet has separate `CreditBucket` (MONTHLY) ✅
3. `monthly_allocation` stored per SchoolCreditAllocation ✅
4. No shared pool logic ✅
5. Teacher A's consumption doesn't affect Teacher B ✅

**Example Flow**:
```
License: 5 teachers, Plan: 20K credits each
↓
Teacher 1: CreditWallet → MONTHLY bucket (20K)
Teacher 2: CreditWallet → MONTHLY bucket (20K)
Teacher 3: CreditWallet → MONTHLY bucket (20K)
Teacher 4: CreditWallet → MONTHLY bucket (20K)
Teacher 5: CreditWallet → MONTHLY bucket (20K)
↓
Teacher 1 uses 10K → balance = 10K
Teacher 2 unaffected → balance = 20K ✅
```

---

## AUDIT TRAIL CORRECTNESS

### ✅ COMPREHENSIVE LOGGING

**All Operations Logged**:
- ✅ License creation
- ✅ Teacher enrollment
- ✅ Teacher removal
- ✅ Rollover applications
- ✅ Bucket expirations
- ✅ Renewal processes

**CreditLedger Entries**:
- ✅ Every grant/consume/refund logged
- ✅ Metadata captures context (license_id, teacher_email, etc.)
- ✅ Immutable ledger — **[UPDATED 2026-09-03]**

**On "immutable".** When this was first written the word described an
intention, not a mechanism: both audit models *documented* themselves as
immutable while `CreditLedger.user` was a `CASCADE` FK that deleted
17,761 ledger rows along with a single production teacher, and nothing
stopped `.update()` / `.delete()` from any code path.

It is now enforced by `billing/immutable.py` — `pre_save` / `pre_delete`
guards plus queryset overrides, with every relation into the audit tables
changed to non-cascading. The section 2 audit re-verified this
empirically rather than by reading: `bulk_update`, related-manager
`update()`/`delete()`, cascade-from-user and cascade-from-wallet are all
refused. One hole was found and closed in that pass —
`bulk_create(update_conflicts=True)` reached an UPDATE through neither
guard (it is not `QuerySet.update()`, and `bulk_create` never emits
`pre_save`) and silently rewrote a settled row's `amount`.

Scope of the guarantee: application level only. Raw SQL, `TRUNCATE`,
`manage.py flush` and direct `psql` access all bypass it, and the app's
DB role is a superuser. See the "WHAT THIS DOES NOT COVER" section of
`billing/immutable.py`.

---

## ERROR HANDLING ANALYSIS

### ✅ COMPREHENSIVE ERROR HANDLING

**Validation Errors** (Raise Exceptions):
- ✅ Invalid plan category
- ✅ Invalid admin user
- ✅ Inactive license during operations
- ✅ Already-enrolled teacher
- ✅ Teacher not found

**Batch Errors** (Graceful):
- ✅ `add_teachers_batch` continues on individual failures
- ✅ Returns detailed error array
- ✅ Tracks success/failure counts

---

## EDGE CASES VERIFIED

### ✅ HANDLED CORRECTLY

1. **Teacher Added to License While Under INDIVIDUAL Subscription**
   - ✅ Old INDIVIDUAL subscription deactivated
   - ✅ Unused credits rolled over
   - ✅ New MONTHLY bucket created

2. **License Replaced While Active**
   - ✅ Old license deactivated
   - ✅ New license created
   - ✅ Teachers can be re-enrolled in new license

3. **Renewal Before Billing Cycle Ends**
   - ✅ Raises ValueError (prevents early renewal)

4. **Renewal with No Unused Credits**
   - ✅ Skips rollover creation
   - ✅ Still creates new MONTHLY bucket

5. **Adding Same Teacher Twice**
   - ✅ Second add returns existing allocation
   - ✅ No duplicate allocations created

6. **Removing Non-Enrolled Teacher**
   - ✅ Raises ValueError (defensive)

7. **Empty Teacher List on Creation**
   - ✅ Creates license with no teachers
   - ✅ Teachers can be added later

---

## PERFORMANCE ANALYSIS

### Database Queries

**create_license_subscription** (with N teachers):
- O(1): Create LicenseSubscription
- O(N): Create SchoolCreditAllocations
- O(N): Create CreditWallets
- O(N): Create CreditBuckets
- O(N): Create CreditLedgers
- **Total**: O(N) - ACCEPTABLE for batch operations up to ~1000 teachers

**process_license_renewal** (with N teachers):
- O(N): Iterate allocations
- O(N): Update buckets
- O(N): Create ledgers
- **Total**: O(N) - Acceptable for monthly task

**Query Optimization**:
- ✅ Uses `select_related()` for ForeignKey optimization
- ✅ Uses `prefetch_related()` for M2M optimization
- ✅ Uses `select_for_update()` for concurrency control

---

## THREAD SAFETY

### ✅ THREAD-SAFE

**Mechanisms**:
- ✅ Django's `@transaction.atomic` ensures isolation
- ✅ `select_for_update()` on critical reads
- ✅ Database locks prevent race conditions
- ✅ Unique constraints prevent duplicates

---

## RECOMMENDATIONS FOR PRODUCTION

1. **Stripe Integration**: Populate `stripe_subscription_id` during license creation
2. **Rate Limiting**: Add rate limits to batch operations (prevent DoS)
3. **Monitoring**: Track license renewals - alert on failures
4. ~~**Celery Tasks**: Create scheduled tasks for `process_license_renewal`~~
   **DONE [2026-09-03]** — `billing.tasks.process_license_renewals` runs
   daily at 00:00 via `CELERY_BEAT_SCHEDULE`, and Beat itself is watched
   by `BEAT_HEALTH_EXPECTATIONS` (a renewal job that silently stops being
   scheduled raises an ERROR within two days).
5. **Admin Dashboard**: Display license analytics (teacher count, credit usage, etc.)

---

## CONCLUSION

### ✅ IMPLEMENTATION IS PRODUCTION-READY

**Summary**:
- ✅ 0 Critical bugs
- ✅ 1 Minor (non-blocking) issue
- ✅ Comprehensive error handling
- ✅ Full transaction safety
- ✅ Correct credit allocation (independent per teacher)
- ✅ Complete audit trail
- ✅ Acceptable performance
- ✅ Thread-safe design

**Verdict**: APPROVED FOR IMPLEMENTATION

---

## Revision note (2026-09-03)

The verdict above is the 2026-06-09 author's. It is kept for the record,
not re-endorsed: it was written before the append-only enforcement
existed and before `validate_admin_user` was hardened, and it described
both as already-correct. Treat the ✅ marks as a snapshot of what was
believed in June, and the **[UPDATED 2026-09-03]** entries as the
verified state.

Re-verified in the section 2 pass: 915 billing tests pass; webhook
signature verification precedes any state change; gunicorn `--timeout`
and `WEBHOOK_REQUEST_HARD_TIMEOUT_SECONDS` are in sync (100s);
`manage.py check` is clean, so `billing.E001` holds and `ATOMIC_REQUESTS`
is off — which the Stripe webhook idempotency claim depends on; bandit
reports 0 issues across `billing/`.
