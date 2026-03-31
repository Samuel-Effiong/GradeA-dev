# Grade-Automator-Plus: Complete Project Architecture & Scope

**Last Updated:** March 30, 2026
**Project Type:** Django 5.2.6 REST API Backend (SaaS Platform)
**Status:** Production-Ready

---

## 📊 Executive Summary

**Grade-Automator-Plus** is a comprehensive, AI-powered SaaS platform for automated assignment grading and educational analytics. It processes assignments in multiple formats (PDFs, images, HTML), extracts structured data using AI vision models and OCR, grades student submissions with AI while respecting teacher-defined rubrics, and provides institution-level analytics tracking adoption and value metrics.

**Core Business Model:**
- Beta program (10M tokens/user) → Commercial subscription tiers
- Credit-based system for AI operations (extraction, grading, creation)
- School-level adoption tracking for institutional sales

---

## 🏗️ System Architecture

### **Technology Stack**

| Layer | Technology | Purpose |
|-------|-----------|---------|
| **Framework** | Django 5.2.6 + DRF 3.16.1 | REST API, ORM, authentication |
| **Database** | PostgreSQL + dj-database-url | Primary data store, JSONB fields |
| **Cache/Queue** | Celery + Redis | Background tasks, scheduled jobs |
| **Authentication** | JWT (djangorestframework_simplejwt) | Stateless API auth |
| **API Docs** | drf-spectacular + Swagger | Interactive OpenAPI documentation |
| **AI Integration** | OpenAI API (via OpenRouter) | GPT-4V for vision/text processing |
| **OCR** | PaddleOCR + Pytesseract | Text extraction from images |
| **PDF Processing** | PyMuPDF (fitz) + pdf2image | PDF parsing and image conversion |
| **Email** | Sendinblue (Brevo) | Transactional emails |
| **Containerization** | Docker + Gunicorn | Production deployment |
| **Monitoring** | Celery Flower + Django Logging | Task monitoring and debugging |

---

## 📁 Project Structure & Django Apps

### **1. Users App** (`/users`)
**Responsibility:** Authentication, authorization, and user lifecycle management

**Key Files:**
- `models.py` - **CustomUser** (UUID pk, email-based auth, role-based)
- `views.py` - Auth endpoints (login, refresh, signup, profile)
- `serializers.py` - User validation and representation
- `permissions.py` - Role-based access control (RBAC)
- `middleware.py` - User activity tracking
- `services.py` - OTP, email verification
- `exceptions.py` - Custom exception handler with logging
- `renderers.py` - Custom response formatting

**Data Models:**
```python
CustomUser
├── id: UUID (primary key)
├── email: EmailField (unique)
├── first_name, middle_name, last_name
├── user_type: STUDENT|TEACHER|SCHOOL_ADMIN|SUPER_ADMIN
├── school: FK → School (null/blank)
├── is_active: Boolean (email verification required)
├── activation_token, activation_expires
├── email_verified_at
├── password_reset_token, password_reset_expires
└── bio, phone, avatar
```

**Key Features:**
- Email-based authentication (no username)
- JWT tokens with refresh mechanism
- OTP-based email verification
- Role-specific permissions and access levels
- User activity logging for analytics
- Password reset and recovery flow

**API Endpoints:**
```
POST   /api/v1/users/register/
POST   /api/v1/auth/login/
POST   /api/v1/auth/refresh/
GET    /api/v1/users/profile/
PUT    /api/v1/users/profile/
POST   /api/v1/users/verify-email/
POST   /api/v1/users/password-reset/
```

---

### **2. Classrooms App** (`/classrooms`)
**Responsibility:** Organizational hierarchy for educational institutions

**Data Models:**

```
School
├── id: UUID
├── name: CharField (unique, indexed)
├── address, phone, website
└── created_at

Session (Academic Period: semester/year/quarter)
├── id: UUID
├── name: CharField (indexed)
├── teacher: FK → CustomUser
└── created_at
└── CONSTRAINT: unique(name, teacher)

Course (Sections/Periods within Session)
├── id: UUID
├── name: CharField (indexed)
├── teacher: FK → CustomUser
├── session: FK → Session
├── description: TextField (indexed)
├── is_active: Boolean (indexed)
├── created_at: DateTimeField (indexed)
└── CONSTRAINT: unique(name, teacher, session)

Topic (Subject areas within Course)
├── id: UUID
├── name: CharField (indexed)
├── course: FK → Course
├── created_at: DateTimeField (indexed)
└── CONSTRAINT: unique(name, course)

StudentCourse (Enrollment Junction)
├── id: UUID
├── student: FK → CustomUser
├── course: FK → Course
├── enrolled_at: DateTimeField
└── CONSTRAINT: unique(student, course)
```

**Key Features:**
- Multi-level hierarchy: School → Session → Course → Topic
- One teacher can manage multiple courses per session
- Students enrolled in multiple courses
- Indexed fields for fast filtering and searching

**API Endpoints:**
```
POST   /api/v1/schools/
GET    /api/v1/schools/{id}/
POST   /api/v1/sessions/
GET    /api/v1/courses/
POST   /api/v1/courses/
GET    /api/v1/courses/{id}/students/
```

---

### **3. Assignments App** (`/assignments`)
**Responsibility:** Assignment CRUD, extraction pipeline, and AI processing orchestration

**Data Model - Assignment:**

```python
Assignment
├── id: UUID (primary key)
├── course: FK → Course (on_delete=CASCADE)
├── topic: FK → Topic (null/blank)
│
├── METADATA
├── title: CharField
├── raw_input: TextField (original uploaded content)
├── raw_input_hash: CharField (for deduplication)
├── created_at: DateTimeField (indexed)
├── due_date: DateTimeField (optional)
│
├── AI-EXTRACTED FIELDS
├── instructions: TextField
├── total_points: IntegerField
├── question_count: IntegerField
├── assignment_type: OBJECTIVE|ESSAY|SHORT-ANSWER|HYBRID
├── questions: JSONField (structured question data)
├── extraction_confidence: IntegerField (0-100)
├── potential_issues: ArrayField (edge cases detected)
├── self_assessment: TextField (AI's own quality assessment)
├── custom_ai_prompt: TextField (override prompt if needed)
│
├── PROCESSING METADATA
├── ai_generated: Boolean
├── ai_raw_payload: JSONField (raw API response)
├── ai_generated_at: DateTimeField
├── was_overridden: Boolean (teacher manually edited)
├── overridden_at: DateTimeField
├── extraction_started_at: DateTimeField
├── extraction_completed_at: DateTimeField
│
└── status: DRAFT|PUBLISHED
```

**Question JSON Structure (standardized format):**

```json
{
  "question_number": 1,
  "question_text": "<p>HTML with <strong>formatting</strong> and $LaTeX$ math</p>",
  "question_type": "ESSAY|OBJECTIVE|SHORT-ANSWER",
  "points": 10,
  "blooms_level": "Remember|Understand|Apply|Analyze|Evaluate|Create",
  "options": [
    "<p>Option A</p>",
    "<p>Option B</p>",
    "<p>Option C</p>",
    "<p>Option D</p>"
  ],
  "rubric": [
    {
      "level": "excellent",
      "points": 10,
      "description": "<p>Student demonstrates <strong>complete understanding</strong>...</p>"
    },
    {
      "level": "good",
      "points": 8,
      "description": "<p>Student demonstrates...</p>"
    },
    {
      "level": "fair",
      "points": 5,
      "description": "<p>Student shows...</p>"
    },
    {
      "level": "poor",
      "points": 0,
      "description": "<p>Student demonstrates minimal...</p>"
    }
  ],
  "model_answer": "<p>The correct answer is...</p>"
}
```

**Key Files:**
- `models.py` - Assignment data model
- `views.py` - REST endpoints for CRUD and extraction
- `serializers.py` - Assignment validation and representation
- `services.py` - Business logic (extraction, processing)
- `tasks.py` - Celery background tasks
- `signals.py` - Auto-trigger extraction on creation
- `urls.py` - API routing

**Extraction Pipeline:**

```
1. User uploads PDF/image/HTML
   ↓
2. PDFService.extract() → extracts raw text + metadata
   ↓
3. Celery Task: extract_assignment_background_task()
   ├─ Encodes image as base64 (for vision models)
   ├─ Loads ASSIGNMENT_EXTRACTION_PROMPT
   └─ Calls AIProcessor.extract_assignment()
   ↓
4. AIProcessor → OpenRouter → GPT-4V
   ├─ Parses response JSON
   ├─ Validates question structure
   ├─ Generates missing rubrics
   ├─ Calculates extraction_confidence
   └─ Records potential_issues
   ↓
5. Update Assignment record with extracted data
   ├─ Set ai_generated=True
   ├─ Set extraction_confidence
   └─ Deduct credits from user wallet
   ↓
6. Webhook notification → frontend (assignment ready)
```

**Credit System Integration:**
- **Extraction**: 1 credit per assignment
- **Rubric generation**: 0.5 credit per missing rubric
- **Grade formatting**: 0.25 credit per submission

---

### **4. Students App** (`/students`)
**Responsibility:** Student submissions and answer processing

**Data Model - StudentSubmission:**

```python
StudentSubmission
├── id: UUID (primary key)
├── assignment: FK → Assignment (on_delete=CASCADE)
├── student: FK → CustomUser (on_delete=CASCADE)
├── submission_date: DateTimeField (auto_now_add)
│
├── STUDENT INPUT
├── raw_input: TextField (original form submission)
├── answers: JSONField (structured answers matching assignment)
│
├── HUMAN GRADING (optional)
├── score: DecimalField (final teacher score)
├── feedback: JSONField (teacher feedback per question)
├── graded_at: DateTimeField
│
├── AI GRADING (automatic)
├── ai_score: DecimalField (AI calculated score)
├── ai_feedback: JSONField (AI feedback per question)
├── ai_graded_at: DateTimeField
├── ai_grading_completed_at: DateField
├── grading_confidence: IntegerField (0-100)
│
├── QUALITY METRICS
├── extraction_confidence: IntegerField (answer extraction quality)
├── was_regraded: Boolean (teacher override AI grade)
├── regraded_at: DateTimeField
├── formatted_grade: TextField
│
└── CONSTRAINT: unique(student, assignment)
```

**Key Features:**
- Unique constraint: one submission per student per assignment
- Support for both AI and teacher grading
- Answer extraction from multiple formats (HTML, text, file uploads)
- Feedback loop for grading confidence metrics
- Batch upload support via `UploadSession`

**API Endpoints:**
```
POST   /api/v1/students/{student_id}/submissions/
GET    /api/v1/students/{student_id}/submissions/
GET    /api/v1/assignments/{assignment_id}/submissions/
PUT    /api/v1/submissions/{submission_id}/
POST   /api/v1/submissions/{submission_id}/regrade/
```

---

### **5. Grading App** (`/grading`)
**Responsibility:** AI-powered grading orchestration (currently minimal - logic in ai_processor)

**Current Status:** Placeholder app with view for grading endpoints

**Future Scope:** Dedicated grading models, rubric application logic, feedback generation

---

### **6. AI Processor App** (`/ai_processor`)
**Responsibility:** AI integration, OCR, and intelligent processing

**Directory Structure:**
```
ai_processor/
├── ASSIGNMENT_EXTRACTION_PROMPT_4_PROSE.txt      (primary extraction prompt)
├── ASSIGNMENT_EXTRACTION_PROMPT_FROM_UPLOADS_HTML.txt (vision-based extraction)
├── ANSWERS_EXTRACTION_PROMPT_HTML_4.txt          (answer extraction from submissions)
├── GRADING_ASSIGNMENT_PROMPT_2.txt               (grading orchestration prompt)
├── ASSIGNMENT_GENERATION_PROMPT_2.txt            (create assignments from text)
├── RUBRIC_EXTRACTION_PROMPT.txt                  (generate rubrics if missing)
├── GRADE_FORMATTER.txt                           (format grades for display)
├── SUPERADMIN_CUSTOM_PROMPT.txt                  (platform health analytics)
├── SUPERADMIN_CUSTOM_PROMPT_2.txt                (advanced metrics)
├── SCHOOLADMIN_CUSTOM_PROMPT.txt                 (school-level analytics)
├── TEACHER_CUSTOM_PROMPT.txt                     (teacher dashboard)
│
├── services.py                                    (AIProcessor class)
├── tools.py                                       (vision encoding, web search)
├── validators.py                                  (JSON validation, logging)
└── models.py                                      (placeholder)
```

**Key Classes - AIProcessor:**

```python
class AIProcessor:
    def __init__(self):
        self.client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=OPENROUTER_API_KEY
        )

    # EXTRACTION METHODS
    def extract_assignment(image_base64, raw_text) → dict
        """
        Vision + text-based assignment extraction
        Returns: {
            title, instructions, total_points, question_count,
            assignment_type, questions[], extraction_confidence,
            potential_issues[], self_assessment
        }
        """

    def extract_answers(submission_raw, assignment_context) → dict
        """
        Extract student answers from raw submission
        Preserves formatting, handles LaTeX, generates confidence scores
        """

    def grade_assignment(assignment, submission) → dict
        """
        AI grading orchestration:
        1. Load assignment structure and rubric
        2. Extract student answers
        3. Question-by-question AI evaluation
        4. Aggregate score and feedback
        5. Generate confidence metrics
        """

    def generate_assignment(prompt_text) → dict
        """
        Create full assignment from text description
        Generates questions, rubrics, model answers
        """

    def get_custom_analytics(model, data) → str
        """
        Role-specific analytics generation
        - SuperAdmin: platform metrics, adoption, retention
        - SchoolAdmin: school adoption, equity analysis
        - Teacher: course analytics, confidence trends
        """
```

**API Integration Details:**

- **Base URL:** `https://openrouter.ai/api/v1`
- **Model:** `gpt-4-vision-preview` or fallback to `claude-3.5-sonnet`
- **Max Tokens:** 4000 (configurable per endpoint)
- **Temperature:** 0.1 (consistent output for structured data)

**Vision Processing Pipeline:**

```python
# Image Encoding (for vision models)
image_bytes → base64_encoded_string

# If PDF:
pdf_bytes → pdf2image → PIL.Image → base64

# If image:
uploaded_file → PIL.Image.open() → base64

# If HTML:
raw_html → lxml.html.parse() → extract text + structure
```

---

### **7. Billing App** (`/billing`)
**Responsibility:** Credit system, subscription management, and usage tracking

**Data Models:**

```
CreditWallet (user balance aggregator)
├── id: UUID
├── user: FK → CustomUser (unique, on_delete=CASCADE)
├── total_available_credits: DecimalField (computed from buckets)
└── last_updated: DateTimeField

CreditBucket (credit allocation pool)
├── id: UUID
├── wallet: FK → CreditWallet
├── bucket_type: MONTHLY|CARRY_OVER|PROMOTIONAL|BETA
├── total_credits: PositiveIntegerField
├── used_credits: PositiveIntegerField
├── remaining_credits: computed property
├── created_at, expires_at: DateTimeField
└── FIFO consumption order

CreditLedger (transaction log - immutable)
├── id: UUID
├── user: FK → CustomUser
├── action_type: GRANT|DEDUCT|REFUND|ADJUSTMENT
├── credits_changed: DecimalField (+ or -)
├── reason: CharField (action description)
├── metadata: JSONField (context, payment ID, etc.)
├── created_at: DateTimeField
└── balance_after: DecimalField (snapshot)

CreditUsageLog (analytics)
├── id: UUID
├── user: FK → CustomUser
├── action_type: EXTRACT|GRADE|GENERATE|FORMAT
├── credits_used: DecimalField
├── feature_used: CharField
├── timestamp: DateTimeField
└── metadata: JSONField

SubscriptionPlan (template)
├── id: UUID
├── name: STANDARD|PRO|POWER|BETA
├── monthly_credits: PositiveIntegerField
├── carry_over_percent: DecimalField
├── carry_over_max: PositiveIntegerField
├── carry_over_expiry_months: PositiveSmallIntegerField
├── overage_block_size: PositiveIntegerField
├── overage_block_price: DecimalField
└── max_overage_blocks: PositiveSmallIntegerField

UserSubscription (active subscription)
├── id: UUID
├── user: FK → CustomUser
├── plan: FK → SubscriptionPlan
├── billing_cycle_start, billing_cycle_end: DateTimeField
├── is_active: Boolean
├── auto_renew: Boolean
├── status: ACTIVE|CANCELED|SUSPENDED
├── cancellation_reason: TextField
├── created_at: DateTimeField
└── CONSTRAINT: one active subscription per user

BetaProfile (beta program tracking)
├── id: UUID
├── user: FK → CustomUser (unique)
├── beta_cohort: CharField (e.g., "cohort_1_2024")
├── token_allocation: 10,000,000 (fixed)
├── tokens_used: IntegerField (aggregated)
├── time_to_cap_days: IntegerField
├── joined_at: DateTimeField
├── status: ACTIVE|CONVERTED|CHURNED
└── conversion_date, churn_date: DateTimeField (nullable)
```

**Subscription Plans (Seed Data):**

| Plan | Monthly Credits | Rollover | Max Rollover | Overage Block | Price/Block |
|------|-----------------|----------|--------------|---------------|-------------|
| BETA | 10M tokens | - | - | - | $0 |
| STANDARD | 100K | 50% | 50K | 10K | $2.50 |
| PRO | 500K | 75% | 100K | 50K | $2.00 |
| POWER | 2M | 100% | 250K | 100K | $1.80 |

**Key Services - SubscriptionService:**

```python
class SubscriptionService:
    @staticmethod
    def activate_subscription(user, plan) → UserSubscription
        """Atomically update subscription + credits"""

    @staticmethod
    def deduct_credits(user, action_type, amount) → bool
        """
        Consume credits in FIFO order (oldest buckets first)
        Raise InsufficientCreditsError if balance < amount
        Log to CreditLedger and CreditUsageLog
        """

    @staticmethod
    def refund_credits(user, action_type, reason) → None
        """Refund failed operations"""

    @staticmethod
    def process_month_renewal(user) → None
        """
        Monthly task:
        1. Archive old monthly bucket
        2. Calculate rollover amount
        3. Create new MONTHLY bucket
        4. Update billing cycle dates
        """

    @staticmethod
    def check_insufficient_credits(user, required) → bool
        """Pre-check before operation"""

class AnalyticsService:
    @staticmethod
    def get_user_usage_stats(user) → dict
        """
        {
            total_used: X,
            usage_by_action: {EXTRACT: X, GRADE: Y, ...},
            daily_usage: [...],
            burn_rate: credits/day,
            days_until_depletion: X
        }
        """

    @staticmethod
    def get_beta_cohort_metrics() → dict
        """
        {
            cohort_size: N,
            median_usage: X,
            p90_usage: Y,
            time_to_cap_avg: Z days,
            conversion_rate: X%,
            feature_mix: {grading: X%, creation: Y%}
        }
        """
```

**API Endpoints:**
```
POST   /api/v1/billing/subscribe/
GET    /api/v1/billing/wallet/
GET    /api/v1/billing/usage/
GET    /api/v1/billing/plans/
POST   /api/v1/billing/refund/
```

---

### **8. Dashboard App** (`/dashboard`)
**Responsibility:** Role-specific analytics and institutional insights

**Key Services - DashboardService:**

```python
class DashboardService:

    # SUPER ADMIN DASHBOARD
    @staticmethod
    def get_platform_health() → dict
        """
        {
            total_users: N,
            active_users_30d: N,
            total_assignments_created: N,
            total_submissions: N,
            total_revenue: $X,
            mrr: $X,
            churn_rate: X%,
            nps_score: X,
            ai_confidence_avg: X%
        }
        """

    @staticmethod
    def get_adoption_metrics() → dict
        """
        School-level adoption tracking:
        {
            schools_by_tier: {basic: N, growing: N, power: N},
            multi_teacher_schools: N,
            avg_teachers_per_school: X,
            avg_courses_per_teacher: X,
            signals: {
                assignments_per_school: [...],
                grade_velocity: [...],
                usage_patterns: {...}
            }
        }
        """

    @staticmethod
    def get_institutional_health() → dict
        """
        Scaling indicators:
        {
            feature_adoption: {extraction: X%, grading: Y%},
            teacher_retention: X%,
            student_engagement: X%,
            support_tickets: N,
            critical_issues: N
        }
        """

    # SCHOOL ADMIN DASHBOARD
    @staticmethod
    def get_school_analytics(school_id) → dict
        """
        {
            school_name: str,
            teacher_count: N,
            student_count: N,
            assignment_count: N,
            avg_grade: X,
            completion_rate: X%,
            equity_metrics: {
                grade_variance: X,
                demographic_breakdown: {...}
            }
        }
        """

    # TEACHER DASHBOARD
    @staticmethod
    def get_course_analytics(course_id) → dict
        """
        {
            course_name: str,
            student_count: N,
            assignments_created: N,
            submissions_received: N,
            avg_score: X,
            grade_distribution: [...],
            ai_grading_confidence: X%,
            grading_time_saved: X hours,
            common_errors: [...]
        }
        """

    @staticmethod
    def get_student_progress(student_id) → dict
        """
        {
            student_name: str,
            courses: N,
            assignments_completed: N,
            avg_score: X,
            score_trends: [...],
            strengths: [...],
            improvement_areas: [...]
        }
        """
```

**Dashboard Analytics Data:**
- **Extraction Confidence:** AI model's confidence in assignment extraction (0-100)
- **Grading Confidence:** AI model's confidence in grade accuracy (0-100)
- **Time Saved:** Calculated as (# graded submissions) × (est. 10 min per submission)
- **Common Errors:** Frequently occurring student mistakes flagged across submissions

**API Endpoints:**
```
GET    /api/v1/dashboard/platform-health/        (SuperAdmin only)
GET    /api/v1/dashboard/adoption-metrics/       (SuperAdmin only)
GET    /api/v1/dashboard/school/{id}/            (School/Super Admin)
GET    /api/v1/dashboard/course/{id}/            (Teacher)
GET    /api/v1/dashboard/student/{id}/           (Student/Teacher)
```

---

### **9. OCR Processor App** (`/ocr_processor`)
**Responsibility:** Optical character recognition and text extraction

**Current Status:** Placeholder app

**Planned Features:**
- PaddleOCR integration for high-accuracy text extraction
- Pytesseract fallback for standard OCR
- Batch processing for large PDF documents
- Language detection and multi-language support
- Confidence scoring per extracted block

---

## 🔄 Request/Response Flow Diagrams

### **Assignment Creation & Extraction Flow**

```
┌─────────────────────────────────────────────────────────────────┐
│ FRONTEND (Teacher)                                              │
│ 1. Upload PDF/image or paste HTML assignment                  │
└────────────────────┬────────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────────┐
│ POST /api/v1/assignments/                                       │
│ {                                                               │
│   course_id: uuid,                                             │
│   title: string,                                               │
│   raw_input: string (PDF/HTML),                                │
│   topic_id: uuid (optional)                                    │
│ }                                                               │
└────────────────────┬────────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────────┐
│ assignments/views.py: AssignmentCreateView                      │
│ ✓ Validate course ownership (teacher)                          │
│ ✓ Create Assignment record (status=DRAFT)                      │
│ ✓ Check credit balance                                         │
│ ✓ Queue extraction task                                        │
└────────────────────┬────────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────────┐
│ Celery Task: extract_assignment_background_task                │
│ 1. If PDF → PDFService.extract() → text + page_count           │
│ 2. If image → encode as base64                                 │
│ 3. Load ASSIGNMENT_EXTRACTION_PROMPT                           │
└────────────────────┬────────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────────┐
│ AIProcessor.extract_assignment()                               │
│                                                                 │
│ POST https://openrouter.ai/api/v1/chat/completions            │
│ {                                                               │
│   model: "gpt-4-vision-preview",                               │
│   messages: [{                                                  │
│     role: "user",                                              │
│     content: [                                                  │
│       {type: "text", text: PROMPT},                            │
│       {type: "image_url", image_url: {url: "base64://..."}}   │
│     ]                                                           │
│   }],                                                           │
│   temperature: 0.1                                             │
│ }                                                               │
└────────────────────┬────────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────────┐
│ Parse JSON Response                                             │
│ {                                                               │
│   title: string,                                               │
│   instructions: HTML string,                                   │
│   total_points: number,                                        │
│   question_count: number,                                      │
│   assignment_type: string,                                     │
│   questions: [{...},{...}],                                    │
│   extraction_confidence: 0-100,                                │
│   potential_issues: [...],                                     │
│   self_assessment: string                                      │
│ }                                                               │
└────────────────────┬────────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────────┐
│ Validators.validate_extraction()                               │
│ ✓ Check all questions parsed                                   │
│ ✓ Verify Bloom's level present                                 │
│ ✓ Check rubric completeness                                    │
│ ✓ Flag missing model answers                                   │
└────────────────────┬────────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────────┐
│ Update Assignment Record                                        │
│ ✓ Set extraction_confidence                                    │
│ ✓ Set questions JSON                                           │
│ ✓ Set potential_issues []                                      │
│ ✓ Set extraction_completed_at                                  │
│ ✓ Set status = PUBLISHED (if confidence > threshold)          │
│ ✓ Deduct credits from wallet                                   │
└────────────────────┬────────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────────┐
│ FRONTEND (WebSocket notification)                              │
│ Assignment ready for review/publishing                         │
│ Confidence score: X%                                           │
│ Potential issues: [...]                                        │
└─────────────────────────────────────────────────────────────────┘
```

### **Student Submission & AI Grading Flow**

```
┌─────────────────────────────────────────────────────────────────┐
│ FRONTEND (Student)                                              │
│ Submit answers to published assignment                         │
└────────────────────┬────────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────────┐
│ POST /api/v1/assignments/{id}/submit/                          │
│ {                                                               │
│   student_id: uuid,                                            │
│   answers: {                                                    │
│     "1": "Student's answer to Q1",                             │
│     "2": "Student's answer to Q2",                             │
│     ...                                                         │
│   },                                                            │
│   raw_input: string (optional, for file uploads)               │
│ }                                                               │
└────────────────────┬────────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────────┐
│ students/views.py: SubmissionCreateView                        │
│ ✓ Create StudentSubmission record                              │
│ ✓ Validate student enrollment in course                        │
│ ✓ Queue grading task                                           │
└────────────────────┬────────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────────┐
│ Celery Task: grade_submission_background_task                  │
│ 1. Check credit balance                                         │
│ 2. Load assignment structure                                    │
│ 3. Extract student answers (preserve formatting)               │
└────────────────────┬────────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────────┐
│ AIProcessor.grade_assignment()                                 │
│                                                                 │
│ For each question in assignment:                               │
│   POST https://openrouter.ai/api/v1/chat/completions          │
│   {                                                             │
│     model: "gpt-4-vision-preview",                             │
│     messages: [{                                                │
│       role: "user",                                            │
│       content: [                                                │
│         {type: "text", text: GRADING_PROMPT},                  │
│         {                                                       │
│           type: "text",                                        │
│           text: f"Question: {question}\\nStudent Answer: {answer}\\nRubric: {rubric}"  │
│         }                                                       │
│       ]                                                         │
│     }]                                                          │
│   }                                                             │
│                                                                 │
│ Returns:                                                        │
│   {                                                             │
│     question_id: 1,                                            │
│     score: 8,                                                  │
│     rubric_level: "good",                                      │
│     feedback: "Student demonstrated...",                      │
│     confidence: 92                                             │
│   }                                                             │
└────────────────────┬────────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────────┐
│ Aggregate Results                                               │
│ - Sum scores: total_score = 95/100                             │
│ - Average confidence: 90%                                       │
│ - Compile feedback for each question                           │
└────────────────────┬────────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────────┐
│ Update StudentSubmission Record                                │
│ ✓ Set ai_score, ai_feedback                                    │
│ ✓ Set grading_confidence                                       │
│ ✓ Set ai_graded_at timestamp                                   │
│ ✓ Set formatted_grade (via GRADE_FORMATTER prompt)            │
│ ✓ Deduct grading credits from wallet                           │
└────────────────────┬────────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────────┐
│ FRONTEND (Student View)                                         │
│ Your score: 95/100 (Grading confidence: 90%)                   │
│                                                                 │
│ Q1: 10/10 ✓                                                     │
│ Feedback: "Excellent understanding of the concept..."          │
│                                                                 │
│ Q2: 8/10                                                        │
│ Feedback: "Good work, but missed edge case..."                 │
│                                                                 │
│ Overall feedback: "Strong performance on this assignment."     │
└─────────────────────────────────────────────────────────────────┘
```

---

## 🗄️ Database Schema (Key Relations)

```
CustomUser (users_customuser)
├── PK: id (UUID)
├── FK: school_id
├── Indexes: email, user_type, is_active
└── Unique: email

School (classrooms_school)
├── PK: id (UUID)
├── Unique: name
└── Indexes: name

Session (classrooms_session)
├── PK: id (UUID)
├── FK: teacher_id (CustomUser)
├── Unique: (name, teacher_id)
└── Indexes: name

Course (classrooms_course)
├── PK: id (UUID)
├── FK: teacher_id, session_id
├── Unique: (name, teacher_id, session_id)
└── Indexes: name, is_active, created_at

Topic (classrooms_topic)
├── PK: id (UUID)
├── FK: course_id
├── Unique: (name, course_id)
└── Indexes: name

StudentCourse (classrooms_studentcourse)
├── PK: id (UUID)
├── FK: student_id (CustomUser), course_id
├── Unique: (student_id, course_id)
└── Indexes: student_id, course_id

Assignment (assignments_assignment)
├── PK: id (UUID)
├── FK: course_id, topic_id
├── JSONB: questions, ai_raw_payload
├── ArrayField: potential_issues
└── Indexes: title, created_at, extraction_completed_at

StudentSubmission (students_studentsubmission)
├── PK: id (UUID)
├── FK: assignment_id, student_id
├── JSONB: answers, feedback, ai_feedback
├── Unique: (student_id, assignment_id)
└── Indexes: graded_at, submission_date

CreditWallet (billing_creditwallet)
├── PK: id (UUID)
├── FK: user_id (unique)
└── Indexes: user_id

CreditBucket (billing_creditbucket)
├── PK: id (UUID)
├── FK: wallet_id
├── Indexes: wallet_id, expires_at

CreditLedger (billing_creditledger)
├── PK: id (UUID)
├── FK: user_id
└── Indexes: user_id, created_at

CreditUsageLog (billing_creditusagelog)
├── PK: id (UUID)
├── FK: user_id
└── Indexes: user_id, timestamp

UserSubscription (billing_usersubscription)
├── PK: id (UUID)
├── FK: user_id, plan_id
└── Indexes: user_id, is_active

SubscriptionPlan (billing_subscriptionplan)
├── PK: id (UUID)
├── Unique: name
└── Hard-coded: 4 plans (STANDARD, PRO, POWER, BETA)

BetaProfile (billing_betaprofile)
├── PK: id (UUID)
├── FK: user_id (unique)
├── Fixed: token_allocation = 10,000,000
└── Indexes: status
```

---

## 🔐 Authentication & Authorization

### **JWT Token Flow**

```
1. User POST /auth/login
   {email, password}
   ↓
2. DRF SimpleJWT validates credentials
   ↓
3. Server returns:
   {
     access: "eyJ0eXAi...",      (expires in 15 min)
     refresh: "eyJ0eXAi..."      (expires in 7 days)
   }
   ↓
4. Client stores tokens in secure storage
   ↓
5. Subsequent requests:
   Authorization: Bearer <access_token>
   ↓
6. If access expired:
   POST /auth/refresh
   {refresh_token}
   ↓
7. New access token issued
```

### **Role-Based Access Control (RBAC)**

```python
UserTypes:
├── STUDENT
│   ├── Can view enrolled courses
│   ├── Can submit assignments
│   ├── Can view own submissions and feedback
│   └── Can view own progress dashboard
│
├── TEACHER
│   ├── Can create/edit courses and assignments
│   ├── Can publish assignments
│   ├── Can view student submissions
│   ├── Can override AI grades
│   ├── Can view course analytics
│   └── Can manage enrollments
│
├── SCHOOL_ADMIN
│   ├── Can view all courses in school
│   ├── Can manage teachers in school
│   ├── Can view school-level analytics
│   ├── Can manage school settings
│   └── Cannot modify individual assignments
│
└── SUPER_ADMIN
    ├── Full platform access
    ├── Can view platform health metrics
    ├── Can view adoption metrics
    ├── Can manage plans and billing
    ├── Can view all users and institutions
    └── Can override any settings
```

---

## 📊 Data Flow & Processing Pipeline

### **Credit Deduction Flow**

```
AIProcessor.extract_assignment()
  │
  └─→ BillingService.deduct_credits(
        user=teacher,
        action_type="EXTRACT",
        amount=1000  # 1 credit = 1000 units
      )
      │
      └─→ CreditWallet.total_available_credits >= 1000?
          │
          ├─ YES: Consume from oldest FIFO bucket
          │       Update bucket.used_credits
          │       Create CreditLedger entry
          │       Create CreditUsageLog entry
          │       Return True
          │
          └─ NO: Raise InsufficientCreditsError
                 Revert task to queue
                 Notify user via email
```

### **Monthly Renewal Task** (Celery Beat)

```
Scheduled: 1st of month at 00:00 UTC

For each user:
  1. Get active UserSubscription
  2. Get current month's MONTHLY bucket
     │
     ├─ If expired or used up:
     │  │
     │  ├─ Calculate rollover from previous bucket
     │  │  └─ rollover_amt = min(
     │  │      unused_credits × (plan.carry_over_percent / 100),
     │  │      plan.carry_over_max
     │  │    )
     │  │
     │  ├─ Create new MONTHLY bucket
     │  │  └─ total_credits = plan.monthly_credits
     │  │
     │  └─ Create new CARRY_OVER bucket (if rollover > 0)
     │     └─ expires_at = now + (plan.carry_over_expiry_months * 30 days)
     │
     └─ Update UserSubscription.billing_cycle_end
```

---

## 🎯 Extraction Quality Metrics

### **Confidence Scoring Algorithm**

```python
extraction_confidence = (
    (has_all_questions ? 30 : 0) +
    (has_all_rubrics ? 30 : 0) +
    (has_model_answers ? 20 : 0) +
    (has_bloom_levels ? 15 : 0) +
    (formatting_intact ? 5 : 0)
)
# Result: 0-100 scale
```

### **Potential Issues Flagging**

- "Multi-page question detected" (question spans PDF pages)
- "Vague rubric descriptor" (e.g., "good performance")
- "Missing model answer for question X"
- "Inconsistent point distribution"
- "Question numbering gap detected"
- "OCR confidence low (< 70%)"
- "Formula/diagram not extractable"

---

## 🚀 Deployment & Infrastructure

### **Docker Setup**

```dockerfile
Base: python:3.12-slim-bookworm

Installed System Packages:
- build-essential (compilation)
- libpq-dev (PostgreSQL driver)
- libgl1, libglib2.0-0 (OpenCV dependencies)
- poppler-utils (PDF to image conversion)
- tesseract-ocr, libtesseract-dev (OCR)

Environment Variables:
- PYTHONUNBUFFERED=1
- PORT=8000
- PADDLE_HOME=/tmp/.paddle
- XDG_CACHE_HOME=/tmp/.cache

User: wagtail (non-root)
WorkDir: /app
```

### **Environment Configurations**

```
ENVIRONMENT = local|dev|prod

If production:
  DEBUG = False
  ALLOWED_HOSTS = [specific domains]
  CSRF_TRUSTED_ORIGINS = [specific domains]

If development:
  DEBUG = False
  ALLOWED_HOSTS = ["*"]
  CORS_ALLOW_ALL_ORIGINS = True

If local:
  DEBUG = True
  CORS_ALLOW_ALL_ORIGINS = True
```

### **Celery Configuration**

```python
CELERY_BROKER_URL = "redis://localhost:6379/0"
CELERY_RESULT_BACKEND = "redis://localhost:6379/0"

CELERY_BEAT_SCHEDULE = {
    "monthly-credit-renewal": {
        "task": "billing.tasks.process_monthly_renewals",
        "schedule": crontab(day_of_month=1, hour=0, minute=0)
    },
    "beta-usage-snapshot": {
        "task": "billing.tasks.snapshot_beta_usage",
        "schedule": crontab(hour=0, minute=0)  # Daily
    }
}
```

---

## 📈 Key Metrics & Analytics

### **Platform Health Dashboard (SuperAdmin)**

```json
{
  "total_users": 5000,
  "active_users_30d": 3200,
  "total_schools": 150,
  "active_schools": 120,
  "total_assignments": 50000,
  "total_submissions": 250000,
  "avg_extraction_confidence": 87.5,
  "avg_grading_confidence": 92.3,
  "mrr": 15000,
  "total_revenue": 180000,
  "churn_rate": 2.1,
  "nps_score": 72,
  "top_issues": [
    {
      "issue": "PDF extraction failing for scanned documents",
      "frequency": 45,
      "status": "in_progress"
    }
  ]
}
```

### **School Adoption Metrics**

```json
{
  "school_name": "Lincoln High School",
  "tier": "power",
  "teacher_count": 25,
  "student_count": 800,
  "assignment_creation_rate": 12.5,  // per week
  "avg_grading_time_saved": 450,     // hours per month
  "ai_adoption_rate": 78.2,          // % of submissions graded by AI
  "value_signals": {
    "feature_mix": {
      "grading": 65,
      "creation": 35
    },
    "velocity": "accelerating",
    "multi_teacher_adoption": true
  }
}
```

### **Beta Program Analytics**

```json
{
  "beta_cohort": "cohort_1_2024",
  "cohort_size": 500,
  "total_allocation": 5000000000,  // 5B tokens across cohort
  "usage_stats": {
    "total_consumed": 2400000000,
    "median_usage": 4500000,
    "p90_usage": 8200000,
    "avg_usage": 4800000
  },
  "time_to_cap": {
    "avg_days": 45,
    "earliest": 12,
    "latest": 89
  },
  "conversion_metrics": {
    "trial_conversions": 125,
    "conversion_rate": 25.0,
    "avg_arpu": 39.99,
    "feature_mix_converters": {
      "grading": 60,
      "creation": 40
    }
  },
  "churn": {
    "churned_users": 75,
    "churn_rate": 15.0,
    "avg_tenure_before_churn": 30
  }
}
```

---

## 🔧 Configuration Files

### **Key Configuration Files**

| File | Purpose |
|------|---------|
| `AutoGrader/settings.py` | Django settings (DB, apps, middleware, Celery) |
| `AutoGrader/urls.py` | URL routing to all apps |
| `AutoGrader/celery.py` | Celery app configuration |
| `AutoGrader/asgi.py` | ASGI config (Uvicorn) |
| `AutoGrader/wsgi.py` | WSGI config (Gunicorn) |
| `.env` | Environment variables (secrets, API keys, DB) |
| `requirements.txt` | Python dependencies (199 packages) |
| `Dockerfile` | Container image configuration |
| `pyproject.toml` | Project metadata and Bandit security config |
| `BRANCHING_STRATEGY.md` | Git workflow documentation |

---

## 📦 Dependencies Overview

### **Core Django Ecosystem**
- `django==5.2.6` - Web framework
- `djangorestframework==3.16.1` - REST API
- `drf-spectacular==0.28.0` - OpenAPI schema
- `django-cors-headers==4.8.0` - CORS support
- `django-environ==0.12.0` - Environment variables
- `djoser==2.3.3` - User management endpoints

### **Authentication & Authorization**
- `djangorestframework-simplejwt==5.5.1` - JWT tokens
- `Authlib==1.6.3` - OAuth2 support

### **Database & ORM**
- `dj-database-url==3.0.1` - Database URL parsing
- `psycopg2-binary` - PostgreSQL driver
- `django-timezone-field==7.2.1` - Timezone support

### **Task Queuing**
- `celery==5.5.3` - Task queue
- `django-celery-beat==2.8.1` - Periodic tasks
- `django-celery-results==2.6.0` - Task result storage
- `flower==2.0.1` - Celery monitoring

### **AI & NLP**
- `openai==1.42.0` - OpenAI API client
- `anthropic==0.67.0` - Claude API (backup)
- `tiktoken==0.7.0` - Token counting for OpenAI

### **Document Processing**
- `PyMuPDF==1.23.3` - PDF text extraction
- `pdf2image==1.16.3` - PDF to image conversion
- `Pillow==10.1.0` - Image processing
- `pytesseract==0.3.10` - OCR via Tesseract
- `paddleocr==2.7.0.3` - PaddleOCR (advanced)

### **Text & Format Conversion**
- `beautifulsoup4==4.14.2` - HTML parsing
- `lxml==4.9.4` - XML/HTML processing
- `prosemirror==0.2.1` - ProseMirror JSON ↔ DOM

### **Development & Testing**
- `pytest==8.2.0` - Testing framework
- `black==25.1.0` - Code formatter
- `flake8==7.3.0` - Linter
- `mypy==1.11.2` - Type checking
- `django-stubs==5.2.8` - Django type hints
- `bandit==1.8.6` - Security checker

---

## 🎓 Educational Features

### **Bloom's Taxonomy Integration**

```python
BloomLevels = [
    "Remember",     # Recall facts
    "Understand",   # Explain concepts
    "Apply",        # Use in new situation
    "Analyze",      # Break into components
    "Evaluate",     # Make judgments
    "Create"        # Build something new
]

Point Distribution Rules:
- Remember:    5-10 points
- Understand:  5-15 points
- Apply:      10-20 points
- Analyze:    15-25 points
- Evaluate:   20-30 points
- Create:     20-30 points
```

### **Rubric Structure**

```json
"rubric": [
  {
    "level": "excellent",
    "points": 10,
    "description": "<p>Student demonstrates <strong>complete understanding</strong> of all key concepts...</p>"
  },
  {
    "level": "good",
    "points": 8,
    "description": "<p>Student demonstrates solid understanding with minor gaps...</p>"
  },
  {
    "level": "fair",
    "points": 5,
    "description": "<p>Student shows basic understanding but significant gaps remain...</p>"
  },
  {
    "level": "poor",
    "points": 0,
    "description": "<p>Student demonstrates minimal or incorrect understanding...</p>"
  }
]
```

---

## 🎯 API Endpoints Summary

### **Authentication**
```
POST   /api/v1/auth/login
POST   /api/v1/auth/refresh
```

### **Users**
```
POST   /api/v1/users/register/
GET    /api/v1/users/profile/
PUT    /api/v1/users/profile/
POST   /api/v1/users/verify-email/
POST   /api/v1/users/password-reset/
```

### **Schools & Organization**
```
GET    /api/v1/schools/
POST   /api/v1/schools/
GET    /api/v1/schools/{id}/
POST   /api/v1/sessions/
GET    /api/v1/courses/
POST   /api/v1/courses/
GET    /api/v1/courses/{id}/students/
```

### **Assignments**
```
POST   /api/v1/assignments/              (create & queue extraction)
GET    /api/v1/assignments/
GET    /api/v1/assignments/{id}/
PUT    /api/v1/assignments/{id}/         (update/publish)
DELETE /api/v1/assignments/{id}/
POST   /api/v1/assignments/{id}/publish/
```

### **Student Submissions**
```
POST   /api/v1/assignments/{id}/submit/
GET    /api/v1/submissions/{id}/
PUT    /api/v1/submissions/{id}/
POST   /api/v1/submissions/{id}/regrade/ (override AI grade)
GET    /api/v1/assignments/{id}/submissions/ (teacher view)
```

### **Billing**
```
GET    /api/v1/billing/plans/
POST   /api/v1/billing/subscribe/
GET    /api/v1/billing/wallet/
GET    /api/v1/billing/usage/
POST   /api/v1/billing/refund/
```

### **Analytics & Dashboard**
```
GET    /api/v1/dashboard/platform-health/    (SuperAdmin only)
GET    /api/v1/dashboard/adoption-metrics/
GET    /api/v1/dashboard/school/{id}/
GET    /api/v1/dashboard/course/{id}/
GET    /api/v1/dashboard/student/{id}/
```

---

## 🔄 Background Tasks (Celery)

```python
# assignments/tasks.py
- extract_assignment_background_task(assignment_id)
- generate_assignment_from_prompt(course_id, prompt_text)

# students/tasks.py
- grade_submission_background_task(submission_id)
- batch_grade_submissions(assignment_id)

# billing/tasks.py
- process_monthly_renewals()
- snapshot_beta_usage()
- process_overage_charges(user_id)

# dashboard/tasks.py
- compute_platform_health_metrics()
- compute_adoption_metrics()
```

---

## 🔐 Security Considerations

### **Implemented**
- ✓ JWT authentication (expires in 15 min)
- ✓ CSRF protection (token-based)
- ✓ CORS configuration
- ✓ Role-based access control (RBAC)
- ✓ Email verification for user activation
- ✓ Password hashing (Django default: PBKDF2)
- ✓ User activity logging for audit trails
- ✓ Secret management via environment variables

### **Planned/Recommended**
- [ ] Rate limiting on API endpoints
- [ ] API key rotation for OpenAI/OpenRouter
- [ ] Webhook signature verification
- [ ] PII data encryption at rest (email, names)
- [ ] Audit logging for sensitive operations
- [ ] DDoS protection (CloudFlare, AWS WAF)
- [ ] Penetration testing and security audit

---

## 📝 Document Format Standards

### **HTML with LaTeX Math**

```html
<!-- Inline math -->
<p>The equation is $E = mc^2$ as Einstein proved.</p>

<!-- Display math -->
<p>The quadratic formula is:</p>
$$x = \frac{-b \pm \sqrt{b^2 - 4ac}}{2a}$$

<!-- Chemical formulas -->
<p>Water is H<sub>2</sub>O with molecular weight 18 g/mol</p>

<!-- Code blocks -->
<pre><code class="language-python">
def fibonacci(n):
    return n if n <= 1 else fibonacci(n-1) + fibonacci(n-2)
</code></pre>
```

### **Extracted Assignment JSON Format (Complete Example)**

```json
{
  "title": "Quantum Mechanics Midterm Exam",
  "instructions": "<p>Answer all questions. Show all work for partial credit.</p>",
  "total_points": 100,
  "question_count": 5,
  "assignment_type": "HYBRID",
  "questions": [
    {
      "question_number": 1,
      "question_text": "<p>What is the <strong>Schrödinger equation</strong>? Write the time-dependent form:</p>\n$$i\\hbar\\frac{\\partial \\psi}{\\partial t} = \\hat{H}\\psi$$",
      "question_type": "SHORT-ANSWER",
      "points": 20,
      "blooms_level": "Understand",
      "options": null,
      "rubric": [
        {
          "level": "excellent",
          "points": 20,
          "description": "<p>Student correctly states equation and explains all terms.</p>"
        },
        {
          "level": "good",
          "points": 16,
          "description": "<p>Student states equation correctly with minor omissions.</p>"
        },
        {
          "level": "fair",
          "points": 10,
          "description": "<p>Student attempts equation with significant errors.</p>"
        },
        {
          "level": "poor",
          "points": 0,
          "description": "<p>No meaningful response.</p>"
        }
      ],
      "model_answer": "<p>The time-dependent Schrödinger equation is:</p>\n$$i\\hbar\\frac{\\partial \\psi}{\\partial t} = \\hat{H}\\psi$$\n<p>Where:</p>\n<ul>\n<li>$\\psi$ is the wavefunction</li>\n<li>$\\hbar = h/(2\\pi)$ is the reduced Planck constant</li>\n<li>$\\hat{H}$ is the Hamiltonian operator (total energy)</li>\n</ul>"
    },
    {
      "question_number": 2,
      "question_text": "<p>Which of the following is <em>NOT</em> a quantum number?</p>",
      "question_type": "OBJECTIVE",
      "points": 10,
      "blooms_level": "Remember",
      "options": [
        "<p>Principal quantum number (n)</p>",
        "<p>Angular momentum quantum number (l)</p>",
        "<p>Magnetic quantum number (m<sub>l</sub>)</p>",
        "<p>Velocity quantum number (v)</p>"
      ],
      "rubric": [
        {
          "level": "excellent",
          "points": 10,
          "description": "<p>Correct answer selected.</p>"
        },
        {
          "level": "poor",
          "points": 0,
          "description": "<p>Incorrect answer selected.</p>"
        }
      ],
      "model_answer": "<p>Answer: D - Velocity quantum number (v) is not a standard quantum number.</p>"
    }
  ]
}
```

---

## 🎯 Project Goals & Success Metrics

### **Short-Term (Q2-Q3 2026)**
- [ ] Beta program reaches 500 active users
- [ ] 50+ schools onboarded
- [ ] Platform health score > 85%
- [ ] AI confidence metrics > 85% (extraction and grading)
- [ ] Teacher time savings > 40 hours/month per school

### **Medium-Term (Q4 2026 - Q1 2027)**
- [ ] Transition 25% of beta users to paid subscriptions
- [ ] MRR > $50,000
- [ ] 200+ schools adopting platform
- [ ] NPS score > 75
- [ ] AI confidence metrics > 90%

### **Long-Term (2027+)**
- [ ] Enterprise features (SSO, custom branding, audit logs)
- [ ] International expansion (multilingual support)
- [ ] Advanced analytics (student trajectory prediction)
- [ ] Integration marketplace (Clever, Google Classroom, Canvas)

---

## 📚 Additional Resources

- **API Documentation:** Available at `/api/v1/swagger-ui/` (drf-spectacular)
- **Branching Strategy:** See `BRANCHING_STRATEGY.md`
- **Contributing Guide:** See documentation
- **License:** MIT

---

**End of Document**
For questions or clarifications, contact the development team.
