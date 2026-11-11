
### activated_after
Keeps users who confirmed their email address at or after this date and time; the time you give is included. A date with no time means the very start of that day (midnight UTC), so that whole day is included. Users who have not confirmed are left out. Works for super admins only; for anyone else it is ignored.

### activated_before
Keeps users who confirmed their email address at or before this date and time; the time you give is included. A date with no time means the very start of that day (midnight UTC), so `activated_before=2026-11-01` leaves out that day itself. Users who have not confirmed are left out. Works for super admins only; for anyone else it is ignored.

### active_last_week
true: keeps teachers who were last active in the past 7 days, counting back from the moment you ask, to the second; exactly 7 days ago still counts. A teacher who was never active counts as false. false: keeps everyone else. Only teachers can have signals; every other user counts as false. Left empty, it does nothing. Works for super admins only; for anyone else it is ignored.

* `true` - true
* `false` - false

### at_80_percent
true: keeps teachers who have been marked as reaching 80% of their beta credits. Once marked, it stays marked. false: keeps everyone else. Only teachers can have signals; every other user counts as false. Left empty, it does nothing. Works for super admins only; for anyone else it is ignored.

* `true` - true
* `false` - false

### enrollments__course
A course ID. Keeps users who are enrolled in this course. Only enrollments you are allowed to see count. If you give two or three of enrollments__course, enrollments__course__session and enrollments__enrollment_status, they must all match the same enrollment. For example, a course and WITHDRAWN keeps students who withdrew from that course, not students who withdrew from another one.

### enrollments__course__isnull
true: keeps users who have no enrollment you can see. false: keeps users who have at least one. Left empty, it does nothing.

### enrollments__course__session
A session ID. Keeps users enrolled in any course of this session. Only enrollments you are allowed to see count. If you give two or three of enrollments__course, enrollments__course__session and enrollments__enrollment_status, they must all match the same enrollment. For example, a course and WITHDRAWN keeps students who withdrew from that course, not students who withdrew from another one.

### enrollments__enrollment_status
Keeps users who have an enrollment with this status. Only enrollments you are allowed to see count. If you give two or three of enrollments__course, enrollments__course__session and enrollments__enrollment_status, they must all match the same enrollment. For example, a course and WITHDRAWN keeps students who withdrew from that course, not students who withdrew from another one.

* `ENROLLED` - Enrolled
* `WITHDRAWN` - Withdrawn
* `COMPLETED` - Completed
* `PENDING` - Pending

### frequent_user
true: keeps teachers who have been active on 8 or more different days. false: keeps everyone else. Only teachers can have signals; every other user counts as false. Left empty, it does nothing. Works for super admins only; for anyone else it is ignored.

* `true` - true
* `false` - false

### has_plan
true: keeps users who have a current plan (a school license seat, a school license they run, or their own active subscription). false: keeps users who have none. Left empty, it does nothing. Works for super admins only; for anyone else it is ignored.

### is_power_grader
true: keeps teachers who have spent more credits on grading than on creating assignments (reading them in and generating them). Equal amounts count as false, and credits spent on feedback are not counted. false: keeps everyone else. Only teachers can have signals; every other user counts as false. Left empty, it does nothing. Works for super admins only; for anyone else it is ignored.

* `true` - true
* `false` - false

### last_login_after
Keeps users who last signed in at or after this date and time; the time you give is included. A date with no time means the very start of that day (midnight UTC), so that whole day is included. Users who never signed in are left out. Works for super admins only; for anyone else it is ignored.

### last_login_before
Keeps users who last signed in at or before this date and time; the time you give is included. A date with no time means the very start of that day (midnight UTC), so `last_login_before=2026-11-01` leaves out that day itself. Users who never signed in are left out. Works for super admins only; for anyone else it is ignored.

### ordering
Sorts the list. Give one or more keys, separated by commas.
The first key sorts the list. Each next key only sorts users who are equal
on the keys before it.

A key on its own sorts lowest first: A to Z, oldest first, smallest first.
Put `-` in front of a key to sort highest first: Z to A, newest first,
largest first.

Example: `-signals,email` puts the users with the most signals first, and
sorts users with the same number by email address.

For every key from `last_login` down, users with an empty value come last,
whichever way you sort. Users who are still equal at the end are sorted by
their ID, so the pages never shuffle. A key that is not on this list is
ignored.

Keys:
- `first_name`: first name.
- `last_name`: last name.
- `email`: email address.
- `last_login`: when the user last signed in.
- `activated_at`: when the user confirmed their email address.
- `plan_tier`: the tier of their current plan, from lowest to highest: Trial,
  Beta, Standard, Pro, Power, Custom.
- `plan_category`: the kind of their current plan, Individual or License, in
  A to Z order.
- `plan_interval`: how often their current plan bills, Annual, Monthly or
  None, in A to Z order.
- `assignments_created`: how many assignments they have made in their
  courses.
- `papers_graded`: how many student papers in their courses have been graded
  by AI.
- `credits_left`: the credits they have left. The list shows credits in
  thousands (1,000 credits show as 1), but sorting uses the exact amount, so
  two users who both show 5 can still be in a set order. Users with no credit
  wallet have an empty value.
- `credits_used`: all the credits they have ever used, not counting
  refunds. Shown and sorted like `credits_left`.
- `signals`: how many of the 4 signals they meet, from 0 to 4
  (at_80_percent, frequent_user, active_last_week, is_power_grader). Only
  teachers have signals; everyone else counts as 0. Users with the same
  number are sorted by conversion score, highest first, with users who have
  no score last, and then by ID. That order for equal numbers stays the same
  with `-signals`.

### page
Page number for pagination

### page_size
Number of results per page

### plan_category
Keeps users whose current plan is of this kind: Individual (their own subscription) or License (a school license). Users with no plan are left out. Works for super admins only; for anyone else it is ignored.

* `INDIVIDUAL` - Individual
* `LICENSE` - License

### plan_interval
Keeps users whose current plan bills this often. Users with no plan are left out. Works for super admins only; for anyone else it is ignored.

* `MONTHLY` - Monthly
* `ANNUAL` - Annual
* `NONE` - None

### plan_tier
Keeps users whose current plan has this tier. The current plan is, in this order: a school license seat as a teacher, a school license they run as its admin, or their own active subscription. Users with no plan are left out. Works for super admins only; for anyone else it is ignored.

* `STANDARD` - Standard
* `PRO` - Pro
* `POWER` - Power
* `BETA` - Beta
* `CUSTOM` - Custom
* `TRIAL` - Trial

### school__name
Keeps only users whose school has exactly this name, with the same capital letters.

### search
A search term.

### signals_min
Keeps users who meet at least this many of the 4 signals (at_80_percent, frequent_user, active_last_week, is_power_grader), from 0 to 4. Only teachers can have signals, so 1 or more keeps teachers only, and 0 keeps everyone. Left empty, it does nothing. Works for super admins only; for anyone else it is ignored.

* `0` - 0
* `1` - 1
* `2` - 2
* `3` - 3
* `4` - 4

### user_type
Keeps only users of this type.

* `STUDENT` - Student
* `TEACHER` - Teacher
* `SCHOOL_ADMIN` - School Admin
* `SUPER_ADMIN` - Super Admin
