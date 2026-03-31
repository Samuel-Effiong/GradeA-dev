# Grade-Automator-Plus: Visual System Diagrams

## 1. System Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          FRONTEND CLIENTS                                   │
│                  (React/Vue.js - Separate Repo)                             │
└────────────┬────────────────────────────────────────────────────────────────┘
             │
             │ HTTPS/REST API calls
             │
┌────────────▼────────────────────────────────────────────────────────────────┐
│                         API GATEWAY & ROUTER                                │
│                    (Gunicorn + ASGI/WSGI)                                   │
│                    [Port 8000]                                              │
└────────────┬────────────────────────────────────────────────────────────────┘
             │
      ┌──────┴──────┬──────────────┬──────────────┬──────────────────┐
      │             │              │              │                  │
      ▼             ▼              ▼              ▼                  ▼
┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐
│  USERS   │  │CLASSROOMS│  │ASSIGNMENTS  │STUDENTS │  │    BILLING       │
│ Django   │  │ & COURSE │  │  & GRADING  │ SUBS.   │  │  & SUBSCRIPTIONS │
│  App     │  │  ORG.    │  │ Management  │ TRACKING│  │   MANAGEMENT     │
└────┬─────┘  └────┬─────┘  └──────┬──────┘  └────┬───┘  └────────┬─────────┘
     │             │               │             │              │
     │ JWT Auth    │ Org Data      │ AI Proc.    │ Submissions  │ Credits
     │ + RBAC      │ + Schools     │ + Rubrics   │ + Answers    │ + Plans
     │             │               │             │              │
     └─────────────┴───────────────┴─────────────┴──────────────┘
                            │
                            ▼
                ┌──────────────────────────┐
                │   DJANGO ORM + SIGNALS   │
                │   (Transaction Manager)  │
                └───────────┬──────────────┘
                            │
        ┌───────────────────┼───────────────────┐
        │                   │                   │
        ▼                   ▼                   ▼
   ┌─────────┐        ┌─────────────┐    ┌─────────┐
   │PostgreSQL       │  Redis Cache │    │ S3/Cloud│
   │Database         │  (Celery)    │    │ Storage │
   │(Primary Data)   │  (Task Queue)│    │ (Files) │
   └─────────┘       └─────────────┘    └─────────┘
        │                   │
        └───────────────────┼───────────────────┘
                            │
        ┌───────────────────┼────────────────┐
        │                   │                │
        ▼                   ▼                ▼
   ┌──────────┐      ┌────────────┐   ┌──────────────┐
   │  Celery  │      │  Celery    │   │    Flower    │
   │  Workers │      │  Beat      │   │  Monitoring  │
   │(Async)   │      │(Scheduler) │   │              │
   └────┬─────┘      └────┬───────┘   └──────────────┘
        │                 │
        ├─────────────────┤
        │                 │
        ▼                 ▼
   ┌──────────────────────────┐
   │  EXTERNAL AI SERVICES    │
   │                          │
   │  ┌────────────────────┐  │
   │  │   OpenRouter.ai    │  │
   │  │  (GPT-4V Vision)   │  │
   │  └────────────────────┘  │
   │                          │
   │  ┌────────────────────┐  │
   │  │  Sendinblue Email  │  │
   │  └────────────────────┘  │
   │                          │
   │  ┌────────────────────┐  │
   │  │  PaddleOCR + OCR   │  │
   │  └────────────────────┘  │
   └──────────────────────────┘
```

## 2. User Role Hierarchy & Permissions

```
┌─────────────────────────────────────────────────────────────────┐
│                     SUPER_ADMIN                                 │
│  • Full platform access                                         │
│  • View all users, schools, data                                │
│  • Manage billing plans & subscriptions                         │
│  • View platform health & metrics                               │
│  • Override any settings                                        │
└────────────────────────┬────────────────────────────────────────┘
                         │
        ┌────────────────┼───────────────┐
        │                │               │
        ▼                ▼               ▼
    ┌──────────┐   ┌──────────┐   ┌──────────┐
    │SCHOOL    │   │ TEACHER  │   │ STUDENT  │
    │ADMIN     │   │          │   │          │
    └────┬─────┘   └────┬─────┘   └────┬─────┘
         │              │              │
         │              │              │
         ▼              ▼              ▼
    ┌──────────────────────────────────────────┐
    │ SCHOOL ADMIN Permissions                 │
    ├──────────────────────────────────────────┤
    │ ✓ View all teachers in school            │
    │ ✓ View school analytics dashboard        │
    │ ✓ View school adoption metrics           │
    │ ✓ Manage school settings                 │
    │ ✗ Cannot modify individual assignments   │
    │ ✗ Cannot override grades                 │
    └──────────────────────────────────────────┘
         │
         │ Reports to
         ▼
    ┌──────────────────────────────────────────┐
    │ TEACHER Permissions                      │
    ├──────────────────────────────────────────┤
    │ ✓ Create/edit courses & sessions         │
    │ ✓ Create/publish assignments             │
    │ ✓ View student submissions                │
    │ ✓ Grade assignments (manually/AI)        │
    │ ✓ Override AI grades                     │
    │ ✓ View course analytics                  │
    │ ✓ Manage course enrollment               │
    │ ✗ Cannot see other teachers' courses     │
    │ ✗ Cannot access billing                  │
    └──────────────────────────────────────────┘
         │
         │ Teaches
         ▼
    ┌──────────────────────────────────────────┐
    │ STUDENT Permissions                      │
    ├──────────────────────────────────────────┤
    │ ✓ View enrolled courses                  │
    │ ✓ View assignments in courses            │
    │ ✓ Submit assignments                     │
    │ ✓ View own submissions & feedback        │
    │ ✓ View own progress dashboard            │
    │ ✗ Cannot see other students' work        │
    │ ✗ Cannot access billing                  │
    │ ✗ Cannot modify grades                   │
    └──────────────────────────────────────────┘
```

## 3. Assignment Extraction Pipeline (Detailed)

```
USER UPLOADS ASSIGNMENT
       │
       ├─ Format Check
       │  ├─ PDF?
       │  ├─ Image?
       │  ├─ HTML?
       │  └─ Text?
       │
       ▼
┌──────────────────────────────────┐
│ POST /api/v1/assignments/        │
│                                  │
│ {                                │
│   course_id: uuid,               │
│   title: "Assignment 1",         │
│   raw_input: <PDF|HTML|text>     │
│ }                                │
└────────┬─────────────────────────┘
         │
         │ Validate:
         │  • Course ownership (teacher)
         │  • Credit balance >= 1
         │
         ▼
    ┌─────────────────┐
    │ Create Assignment│
    │ status = DRAFT  │
    │ id = uuid1234   │
    └────────┬────────┘
             │
             │ Dequeue Signal
             │
             ▼
┌────────────────────────────────────┐
│ Celery Task:                       │
│ extract_assignment_background()    │
│                                    │
│ extract_started_at = now           │
└────────┬───────────────────────────┘
         │
         ├─ IF PDF:
         │  └─ PDFService.extract()
         │     ├─ fitz.open(pdf_bytes)
         │     ├─ Extract text from each page
         │     └─ Return: {text, page_count}
         │
         ├─ IF Image:
         │  └─ PIL.Image.open()
         │     └─ Encode as base64
         │
         └─ IF HTML:
            └─ lxml.html.parse()
               └─ Extract text + structure
         │
         ▼
    ┌──────────────────────┐
    │ Load Extraction      │
    │ Prompt Template:     │
    │ ASSIGNMENT_EXTRACT   │
    │ PROMPT_4_PROSE.txt   │
    │ (or vision variant)  │
    └──────────┬───────────┘
               │
               │ Create Message
               │
               ▼
    ┌─────────────────────────────────┐
    │ AIProcessor.extract_assignment()│
    │                                 │
    │ POST https://openrouter.ai/ ... │
    │ {                               │
    │   model: "gpt-4-vision",        │
    │   messages: [{                  │
    │     role: "user",               │
    │     content: [                  │
    │       {text: PROMPT},           │
    │       {image_url: base64://...} │
    │     ]                           │
    │   }],                           │
    │   temperature: 0.1,             │
    │   max_tokens: 4000              │
    │ }                               │
    └────────┬──────────────────────────┘
             │
             │ Parse Response JSON
             │
             ▼
    ┌──────────────────────────────┐
    │ Extract JSON with:            │
    │ • title                       │
    │ • instructions (HTML)         │
    │ • total_points                │
    │ • question_count              │
    │ • assignment_type             │
    │ • questions[]:                │
    │   - question_number           │
    │   - question_text (HTML+LaTeX)│
    │   - question_type             │
    │   - points                    │
    │   - blooms_level              │
    │   - options[] (for OBJECTIVE) │
    │   - rubric[] (4 levels)       │
    │   - model_answer (HTML)       │
    │ • extraction_confidence (0-100)
    │ • potential_issues []         │
    │ • self_assessment (AI notes)  │
    └────────┬─────────────────────┘
             │
             │ Validation
             │
             ▼
    ┌──────────────────────────────┐
    │ validators.validate()         │
    │                               │
    │ ✓ All questions parsed?       │
    │ ✓ Rubrics complete?           │
    │ ✓ Model answers present?      │
    │ ✓ Bloom's levels assigned?    │
    │ ✓ Point distribution valid?   │
    │                               │
    │ Flags:                        │
    │ ⚠ "Multi-page question"       │
    │ ⚠ "Vague rubric descriptor"   │
    │ ⚠ "Missing model answer"      │
    │ ⚠ "Inconsistent points"       │
    └────────┬─────────────────────┘
             │
             ▼
    ┌─────────────────────────────┐
    │ Update Assignment Record:   │
    │                             │
    │ ai_generated = True         │
    │ ai_raw_payload = {}         │
    │ ai_generated_at = now       │
    │ questions = JSON            │
    │ extraction_confidence = X   │
    │ potential_issues = []       │
    │ extraction_completed = now  │
    │                             │
    │ If confidence > 60:         │
    │   status = PUBLISHED        │
    │ Else:                       │
    │   status = DRAFT            │
    └────────┬────────────────────┘
             │
             │ Deduct Credits
             │
             ▼
    ┌─────────────────────────────┐
    │ BillingService.deduct()     │
    │                             │
    │ action_type = "EXTRACT"     │
    │ credits = 1000 (1 credit)   │
    │                             │
    │ CreditWallet → oldest bucket│
    │ bucket.used_credits += 1000 │
    │                             │
    │ Log:                        │
    │ • CreditLedger entry        │
    │ • CreditUsageLog entry      │
    │ • balance_after = X         │
    └────────┬────────────────────┘
             │
             ▼
    ┌─────────────────────────────┐
    │ Notify Frontend              │
    │                             │
    │ WebSocket Event:             │
    │ "assignment.extraction      │
    │  .completed"                │
    │                             │
    │ {                           │
    │   assignment_id: uuid,      │
    │   status: "PUBLISHED",      │
    │   confidence: 87,           │
    │   issues: [...]             │
    │ }                           │
    └─────────────────────────────┘
```

## 4. Student Submission & AI Grading Pipeline

```
STUDENT SUBMITS ANSWERS
       │
       ▼
┌──────────────────────────────────────┐
│ POST /api/v1/assignments/{id}/submit/│
│                                      │
│ {                                    │
│   student_id: uuid,                  │
│   answers: {                         │
│     "1": "Student's answer to Q1",  │
│     "2": "Student's answer to Q2",  │
│     ...                              │
│   },                                 │
│   raw_input: "Optional file upload"  │
│ }                                    │
└────────┬─────────────────────────────┘
         │
         │ Validate:
         │  • Student enrolled in course?
         │  • Assignment published?
         │  • No duplicate submission?
         │
         ▼
    ┌──────────────────────────┐
    │ Create StudentSubmission │
    │ status = SUBMITTED       │
    │ submission_date = now    │
    │ answers = JSON           │
    └────────┬─────────────────┘
             │
             │ Queue Task
             │
             ▼
┌─────────────────────────────────────┐
│ Celery Task:                        │
│ grade_submission_background()       │
│                                     │
│ Check credit balance >= 1           │
└────────┬────────────────────────────┘
         │
         │ Load Assignment Context
         │
         ▼
    ┌──────────────────────────────────┐
    │ Extract Answer Format:            │
    │                                  │
    │ For each question:               │
    │  1. Get raw student answer       │
    │  2. If image/file → OCR/parse    │
    │  3. Preserve formatting (HTML)   │
    │  4. Map to question structure    │
    │  5. Validate answer format       │
    │  6. Calculate extraction_conf.   │
    └────────┬─────────────────────────┘
             │
             │ For Each Question:
             │
             ▼
    ┌─────────────────────────────────┐
    │ AIProcessor.grade_question()    │
    │                                 │
    │ POST https://openrouter.ai/... │
    │ {                               │
    │   model: "gpt-4-vision",        │
    │   messages: [{                  │
    │     role: "user",               │
    │     content: [                  │
    │       {text: GRADING_PROMPT},   │
    │       {text: "Question: " + Q}, │
    │       {text: "Student: " + A},  │
    │       {text: "Rubric: " + R}    │
    │     ]                           │
    │   }],                           │
    │   temperature: 0.1              │
    │ }                               │
    └────────┬──────────────────────────┘
             │
             │ Parse Response
             │
             ▼
    ┌───────────────────────────────┐
    │ Question Grade Result:        │
    │ {                             │
    │   question_id: 1,             │
    │   score: 8/10,                │
    │   rubric_level: "good",       │
    │   feedback: "HTML text...",   │
    │   reasoning: "AI explanation",│
    │   confidence: 92              │
    │ }                             │
    └───────────────────────────────┘
             │
             │ Repeat for all questions
             │
             ▼
    ┌───────────────────────────────┐
    │ Aggregate Results:            │
    │                               │
    │ total_score = sum(q_scores)   │
    │ avg_confidence = avg(q_confs) │
    │ feedback_obj = {q1, q2, ...}  │
    │ graded_at = now               │
    └────────┬──────────────────────┘
             │
             │ Format Grade Display
             │
             ▼
    ┌────────────────────────────────┐
    │ AIProcessor.format_grade()     │
    │                                │
    │ Uses GRADE_FORMATTER.txt       │
    │                                │
    │ Generates:                     │
    │ • Congratulatory message       │
    │ • Strength summary             │
    │ • Improvement areas            │
    │ • Study recommendations        │
    │ • Overall assessment           │
    └────────┬───────────────────────┘
             │
             ▼
    ┌─────────────────────────────────┐
    │ Update StudentSubmission:       │
    │                                 │
    │ ai_score = 80                   │
    │ ai_feedback = {q1, q2, ...}     │
    │ ai_graded_at = now              │
    │ grading_confidence = 92         │
    │ formatted_grade = "..."         │
    │ was_regraded = False            │
    └────────┬────────────────────────┘
             │
             │ Deduct Grading Credits
             │
             ▼
    ┌─────────────────────────────────┐
    │ BillingService.deduct()         │
    │                                 │
    │ action_type = "GRADE"           │
    │ credits = 500 (0.5 credit)      │
    │ per_assignment_multiplier = 1   │
    │                                 │
    │ Log transactions                │
    └────────┬────────────────────────┘
             │
             ▼
    ┌────────────────────────────────┐
    │ Send Notifications              │
    │                                │
    │ 1. Email to student:            │
    │    "Your assignment has been    │
    │     graded!"                   │
    │                                │
    │ 2. WebSocket to teacher:        │
    │    "New submission graded"      │
    │                                │
    │ 3. Update dashboard metrics     │
    └────────────────────────────────┘
```

## 5. Credit System Lifecycle

```
USER LIFECYCLE WITH CREDITS

NEW USER → BETA PROGRAM
    │
    ├─ BetaProfile.create()
    │  └─ token_allocation = 10,000,000
    │     (10M tokens ≈ ~10 credits)
    │
    ▼
CREDIT WALLET INITIALIZED
    │
    ├─ CreditWallet.create()
    └─ CreditBucket[BETA].create()
       └─ total_credits = 10,000,000 tokens
          expires_at = NULL (no expiry)
    │
    ▼
USING CREDITS IN BETA PROGRAM
    │
    ├─ Extract Assignment
    │  └─ Deduct 1,000 credits (FIFO from BETA bucket)
    │
    ├─ Grade Submission
    │  └─ Deduct 500 credits
    │
    └─ Create Assignment
       └─ Deduct 1,500 credits
    │
    ▼
MONITORING BETA USAGE
    │
    ├─ DailyTask: snapshot_beta_usage()
    │  ├─ tokens_used += deductions
    │  ├─ time_to_cap = (time to deplete 10M tokens)
    │  └─ If tokens_used > 10M:
    │     └─ status = "CONVERTED" (to paid plan)
    │
    ▼
CONVERSION TO PAID PLAN
    │
    ├─ User selects "Subscribe to Pro"
    │  │
    │  ▼
    │ ┌──────────────────────────────┐
    │ │ SubscriptionService.activate()│
    │ │                              │
    │ │ 1. Create UserSubscription   │
    │ │    plan = PRO                │
    │ │    billing_cycle_start = now │
    │ │    billing_cycle_end = +1mo  │
    │ │    is_active = True          │
    │ │                              │
    │ │ 2. Archive old BETA bucket   │
    │ │                              │
    │ │ 3. Create new MONTHLY bucket │
    │ │    total_credits = 500,000   │
    │ │    (PRO plan grants 500k/mo) │
    │ │                              │
    │ │ 4. Handle rollover           │
    │ │    unused = BETA.remaining   │
    │ │    rollover = min(           │
    │ │      unused × 75%,           │
    │ │      100k (max_rollover)     │
    │ │    )                         │
    │ │    if rollover > 0:          │
    │ │      Create CARRY_OVER bucket│
    │ │      expires_at = +6 months  │
    │ │                              │
    │ │ 5. Update wallet             │
    │ │                              │
    │ │ 6. Log to ledger             │
    │ └──────────────────────────────┘
    │
    ▼
MONTHLY CREDIT RENEWAL
    │
    ├─ Celery Beat: 1st of month, 00:00 UTC
    │  │
    │  ├─ Get all active subscriptions
    │  │
    │  └─ For each user:
    │     │
    │     ├─ Current MONTHLY bucket depleted?
    │     │  │
    │     │  ├─ YES:
    │     │  │  ├─ Calculate rollover from expired
    │     │  │  │  └─ rollover = min(
    │     │  │  │     unused × plan.carry_over_%,
    │     │  │  │     plan.carry_over_max
    │     │  │  │  )
    │     │  │  ├─ Create new MONTHLY bucket
    │     │  │  │  └─ total_credits = plan.monthly
    │     │  │  │     expires_at = end_of_month
    │     │  │  └─ Create CARRY_OVER bucket
    │     │  │     └─ expires_at = +6 months
    │     │  │
    │     │  └─ NO: Still has credits, wait
    │     │
    │     └─ Update billing_cycle dates
    │
    ▼
OVERAGE HANDLING
    │
    ├─ User exceeds monthly credits
    │  │
    │  ├─ Check credits available:
    │  │  balance < 500?
    │  │
    │  └─ Insufficient → Raise InsufficientCreditsError
    │
    ├─ Automatic overage blocks (if enabled)
    │  │
    │  ├─ Plan.overage_block_size = 100,000 credits
    │  ├─ Plan.overage_block_price = $29.99
    │  └─ Auto charge when balance < 1 block
    │
    ▼
CHURN / CANCELLATION
    │
    ├─ User cancels subscription
    │  │
    │  ├─ UserSubscription.status = "CANCELED"
    │  ├─ is_active = False
    │  ├─ cancellation_reason = "..."
    │  └─ cancellation_date = now
    │
    ├─ Remaining credits:
    │  └─ Option 1: Forfeited (default)
    │     Option 2: Refunded (via Stripe)
    │
    └─ BetaProfile.status = "CHURNED"
       └─ churn_date = now
          avg_tenure = now - joined_date
```

## 6. Data Model Relationships (ER Diagram)

```
┌──────────────────┐
│   CustomUser     │
├──────────────────┤
│ id: UUID (PK)    │
│ email (unique)   │
│ user_type        │
│ school_id (FK)   │◄──────────────┐
│ is_active        │               │
│ password_hash    │               │
└──────────────────┘               │
       ▲                           │
       │ ┌──────────────┐          │
       │ │              │          │
       │ ├─ teacher_id──┼──┐       │
       │ │              │  │       │
       │ └──────────────┘  │       │
       │                   │       │
    ┌──▼──────────────┐    │       ▼
    │   Session       │    │   ┌─────────────┐
    ├────────────────┤    │   │   School    │
    │ id: UUID (PK)  │    │   ├─────────────┤
    │ teacher_id (FK)├────┘   │ id: UUID    │
    │ name           │        │ name        │
    │ created_at     │        │ address     │
    └────┬───────────┘        └─────────────┘
         │ 1
         │ n
         │ ┌───────────────────┐
         │ │  Course           │
         │ ├───────────────────┤
         │ │ id: UUID (PK)     │
         │ │ teacher_id (FK)   │
         │ │ session_id (FK)   │
         │ │ name              │
         │ │ description       │
         │ │ is_active         │
         │ │ created_at        │
         │ └────┬──────────────┘
         │      │ 1
         │      │ n
         │      │ ┌──────────────────┐
         │      │ │  Topic           │
         │      │ ├──────────────────┤
         │      │ │ id: UUID (PK)    │
         │      │ │ course_id (FK)   │
         │      │ │ name             │
         │      │ │ created_at       │
         │      │ └────┬─────────────┘
         │      │      │ 1
         │      │      │ n
         │      │      │ ┌──────────────────┐
         │      │      │ │  Assignment      │
         │      │      │ ├──────────────────┤
         │      │      │ │ id: UUID (PK)    │
         │      │      │ │ course_id (FK)   │
         │      │      │ │ topic_id (FK)    │
         │      │      │ │ title            │
         │      │      │ │ questions (JSON) │
         │      │      │ │ status: DRAFT|PUB
         │      │      │ │ ai_generated     │
         │      │      │ │ extraction_conf  │
         │      │      │ └────┬─────────────┘
         │      │      │      │ 1
         │      │      │      │ n
         │      │      │      │ ┌──────────────────────┐
         │      │      │      │ │ StudentSubmission    │
         │      │      │      │ ├──────────────────────┤
         │      │      │      │ │ id: UUID (PK)        │
         │      │      │      │ │ assignment_id (FK)   │
         │      │      │      │ │ student_id (FK)      │
         │      │      │      │ │ answers (JSON)       │
         │      │      │      │ │ ai_score             │
         │      │      │      │ │ ai_feedback (JSON)   │
         │      │      │      │ │ grading_confidence   │
         │      │      │      │ │ submission_date      │
         │      │      │      │ └──────────────────────┘
         │      │      │
         │      └──────┴────────┘
         │
         ├─ student_id
         │  │
         │  ▼
         │  ┌──────────────────────┐
         │  │ StudentCourse        │
         │  ├──────────────────────┤
         │  │ id: UUID (PK)        │
         │  │ student_id (FK) ─────┤────┐
         │  │ course_id (FK) ──────┤────┤─────┘
         │  │ enrolled_at          │    │
         │  └──────────────────────┘    │
         │                              │
         └──────────────────────────────┘

BILLING RELATIONSHIPS:

CustomUser (id)
       │
       │ 1:1
       ▼
┌──────────────────────────┐
│   CreditWallet           │
├──────────────────────────┤
│ id: UUID (PK)            │
│ user_id (FK, unique)     │
│ total_available (computed)
└────┬─────────────────────┘
     │ 1
     │ n (FIFO consumption)
     ▼
┌──────────────────────────┐
│   CreditBucket           │
├──────────────────────────┤
│ id: UUID (PK)            │
│ wallet_id (FK)           │
│ bucket_type: MONTHLY|    │
│   CARRY_OVER|PROMO|BETA  │
│ total_credits            │
│ used_credits             │
│ remaining (computed)     │
│ created_at, expires_at   │
└──────────────────────────┘

CustomUser (id)
       │
       │ 1:n
       ▼
┌──────────────────────────┐
│   CreditLedger           │
├──────────────────────────┤
│ id: UUID (PK)            │
│ user_id (FK)             │
│ action_type: GRANT|      │
│   DEDUCT|REFUND|ADJUST   │
│ credits_changed          │
│ balance_after (snapshot) │
│ reason                   │
│ created_at               │
└──────────────────────────┘

CustomUser (id)
       │
       │ 1:n
       ▼
┌──────────────────────────┐
│   CreditUsageLog         │
├──────────────────────────┤
│ id: UUID (PK)            │
│ user_id (FK)             │
│ action_type: EXTRACT|    │
│   GRADE|GENERATE|FORMAT  │
│ credits_used             │
│ feature_used             │
│ timestamp                │
│ metadata (JSON)          │
└──────────────────────────┘

CustomUser (id)
       │
       │ 1:n (but usually 1:1)
       ▼
┌────────────────────────────┐
│   UserSubscription         │
├────────────────────────────┤
│ id: UUID (PK)              │
│ user_id (FK)               │
│ plan_id (FK) ────┐         │
│ billing_cycle_   │         │
│  start/end       │         │
│ is_active        │         │
│ auto_renew       │         │
│ status: ACTIVE|  │         │
│   CANCELED       │         │
│ created_at       │         │
└────────────────────────────┘
                   │
                   │ 1:1
                   ▼
            ┌─────────────────┐
            │ SubscriptionPlan│
            ├─────────────────┤
            │ id: UUID (PK)   │
            │ name: STANDARD, │
            │   PRO, POWER    │
            │ monthly_credits │
            │ carry_over_%    │
            │ overage_block   │
            │ price/block     │
            └─────────────────┘

CustomUser (id)
       │
       │ 1:1
       ▼
┌──────────────────────────┐
│   BetaProfile            │
├──────────────────────────┤
│ id: UUID (PK)            │
│ user_id (FK, unique)     │
│ beta_cohort              │
│ token_allocation: 10M    │
│ tokens_used              │
│ time_to_cap_days         │
│ status: ACTIVE|          │
│   CONVERTED|CHURNED      │
│ joined_at, conversion_at │
│ churn_at                 │
└──────────────────────────┘
```

## 7. Celery Task Queue & Beat Schedule

```
                    ┌──────────────────┐
                    │  Redis Broker    │
                    │  6379 (default)  │
                    └────────┬─────────┘
                             │
            ┌────────────────┼────────────────┐
            │                │                │
            ▼                ▼                ▼
     ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
     │ Celery Beat  │ │ Celery Worker│ │ Celery Worker│
     │ (Scheduler)  │ │ Instance 1   │ │ Instance N   │
     └──────┬───────┘ └──────┬───────┘ └──────┬───────┘
            │                │                │
            │ Schedules      │ Consumes       │ Consumes
            │ periodic tasks │ tasks          │ tasks
            │                │                │
            ▼                ▼                ▼
     ┌──────────────────────────────────────────────┐
     │ TASK QUEUES (by priority)                    │
     ├──────────────────────────────────────────────┤
     │                                              │
     │ high_priority: [extract_assignment, ...]    │
     │ default: [grade_submission, ...]            │
     │ low_priority: [analytics_snapshot, ...]     │
     │                                              │
     └──────────────────────────────────────────────┘
            │                │                │
            │                │                │
            ▼                ▼                ▼
     ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
     │ Results      │ │ Results      │ │ Results      │
     │ Backend:     │ │ Backend:     │ │ Backend:     │
     │ Redis        │ │ Redis        │ │ Redis        │
     │ (persist     │ │ (persist     │ │ (persist     │
     │  for 1 day)  │ │  for 1 day)  │ │  for 1 day)  │
     └──────────────┘ └──────────────┘ └──────────────┘
            │                │                │
            └────────────────┼────────────────┘
                             │
                             ▼
                    ┌──────────────────┐
                    │  Flower Monitor  │
                    │  (Port 5555)     │
                    │                  │
                    │ View task stats, │
                    │ execution logs   │
                    └──────────────────┘

SCHEDULED TASKS (via Django Celery Beat):

┌────────────────────────────────────────────────────────┐
│ Celery Beat Schedule (/AutoGrader/settings.py)         │
├────────────────────────────────────────────────────────┤
│                                                        │
│ 1. monthly-credit-renewal                             │
│    ├─ Task: billing.tasks.process_monthly_renewals()│
│    ├─ Schedule: 1st of month, 00:00 UTC              │
│    └─ Action: Archive old buckets, grant new credits │
│                                                        │
│ 2. beta-usage-snapshot (DAILY)                       │
│    ├─ Task: billing.tasks.snapshot_beta_usage()      │
│    ├─ Schedule: Every day, 00:00 UTC                 │
│    └─ Action: Snapshot token usage for cohort        │
│                                                        │
│ 3. platform-health-metrics                           │
│    ├─ Task: dashboard.tasks.compute_health()         │
│    ├─ Schedule: Every hour                           │
│    └─ Action: Aggregate platform KPIs                │
│                                                        │
│ 4. adoption-metrics-update                           │
│    ├─ Task: dashboard.tasks.compute_adoption()       │
│    ├─ Schedule: Every 6 hours                        │
│    └─ Action: School-level adoption signals          │
│                                                        │
│ 5. process-overage-charges                           │
│    ├─ Task: billing.tasks.process_overage()          │
│    ├─ Schedule: Daily at 02:00 UTC                   │
│    └─ Action: Auto-charge overage blocks             │
│                                                        │
└────────────────────────────────────────────────────────┘

IMMEDIATE TASKS (queued on action):

┌────────────────────────────────────────────────────────┐
│ Queued on User Action                                  │
├────────────────────────────────────────────────────────┤
│                                                        │
│ extract_assignment_background_task()                  │
│  Trigger: User creates assignment                     │
│  Queue: high_priority                                │
│  Timeout: 120 seconds                                │
│  Retry: 3 attempts, 30s backoff                       │
│                                                        │
│ grade_submission_background_task()                    │
│  Trigger: Student submits assignment                 │
│  Queue: default                                      │
│  Timeout: 180 seconds                                │
│  Retry: 3 attempts                                   │
│                                                        │
│ generate_assignment_from_prompt()                     │
│  Trigger: Teacher creates from text                  │
│  Queue: high_priority                                │
│  Timeout: 120 seconds                                │
│                                                        │
└────────────────────────────────────────────────────────┘
```

## 8. Request/Response Cycle Example

```
CLIENT (Frontend)                    SERVER (Django)

    │                                    │
    ├─ POST /api/v1/auth/login        │
    │  {email, password}              │
    ├─────────────────────────────────>│
    │                                  │
    │                                  ├─ Check credentials
    │                                  ├─ Generate JWT tokens
    │                                  ├─ Log login event
    │                                  │
    │  {access, refresh}              │
    │<─────────────────────────────────┤
    │                                  │
    ├─ Store access in sessionStorage │
    │                                  │
    │ Subsequent request:              │
    │ GET /api/v1/courses/             │
    │ Authorization: Bearer {access}   │
    ├─────────────────────────────────>│
    │                                  │
    │                                  ├─ JWTAuthentication
    │                                  │  │ Verify token
    │                                  │  │ Extract user
    │                                  │  │ Check permissions
    │                                  │  └─ user = CustomUser
    │                                  │
    │                                  ├─ CourseViewSet.list()
    │                                  │  │
    │                                  │  ├─ courses = Course.objects.filter(
    │                                  │  │   teacher=user
    │                                  │  │  )
    │                                  │  │
    │                                  │  └─ Serialize to JSON
    │                                  │
    │  [{id, name, ...}, ...]         │
    │<─────────────────────────────────┤
    │                                  │
    │ User selects course              │
    │ GET /api/v1/courses/uuid/       │
    ├─────────────────────────────────>│
    │                                  │
    │                                  ├─ Check permissions
    │                                  ├─ Fetch course data
    │                                  ├─ Get related assignments
    │                                  │
    │  {id, name, students, ...}      │
    │<─────────────────────────────────┤
    │                                  │
    │ Teacher uploads assignment      │
    │ POST /api/v1/assignments/       │
    │ {course_id, raw_input: PDF}     │
    ├─────────────────────────────────>│
    │                                  │
    │                                  ├─ Create Assignment
    │                                  ├─ Check credit balance
    │                                  ├─ Queue extraction task
    │                                  │
    │                                  ├─ Celery Task:
    │                                  │  extract_assignment_bg()
    │                                  │  │ (processing...)
    │                                  │
    │  {id, status: "DRAFT"}          │
    │<─────────────────────────────────┤
    │                                  │
    │ Frontend polls for progress     │
    │ (or WebSocket update):          │
    │ GET /api/v1/assignments/{id}/  │
    ├─────────────────────────────────>│
    │                                  │
    │                                  ├─ Return current state
    │                                  │  {status: "processing"}
    │                                  │
    │  {status: "processing", ...}    │
    │<─────────────────────────────────┤
    │                                  │
    │ ... wait ...                     │
    │                                  │
    │ Poll again:                      │
    │ GET /api/v1/assignments/{id}/  │
    ├─────────────────────────────────>│
    │                                  │
    │                                  ├─ Extraction complete!
    │                                  │  Return: {
    │                                  │   status: "PUBLISHED",
    │                                  │   questions: [...],
    │                                  │   confidence: 87
    │                                  │  }
    │                                  │
    │  {status: "PUBLISHED", ...}     │
    │<─────────────────────────────────┤
    │                                  │
    │ Display assignment to students  │
    │                                  │
```

---

**End of Diagrams Document**
