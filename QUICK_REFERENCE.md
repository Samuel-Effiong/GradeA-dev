# Grade-Automator-Plus: Quick Reference Guide

## 🎯 Project at a Glance

| Aspect | Details |
|--------|---------|
| **Project** | Grade-Automator-Plus (Educational SaaS) |
| **Framework** | Django 5.2.6 + Django REST Framework |
| **Database** | PostgreSQL (JSONB fields for flexible data) |
| **Task Queue** | Celery + Redis + Celery Beat |
| **Authentication** | JWT (djangorestframework_simplejwt) |
| **AI Integration** | OpenRouter (GPT-4V Vision) |
| **OCR** | PaddleOCR + Pytesseract |
| **Container** | Docker + Gunicorn |
| **Status** | Production-ready |

---

## 📦 Main Django Apps Overview

### Users
- **Purpose**: Authentication, authorization, user management
- **Key Models**: CustomUser (UUID, email-based, role-based)
- **Key Features**: JWT tokens, OTP verification, RBAC
- **API Prefix**: `/api/v1/users/`, `/api/v1/auth/`

### Classrooms
- **Purpose**: Organizational hierarchy (schools, sessions, courses, topics)
- **Key Models**: School, Session, Course, Topic, StudentCourse
- **Key Features**: Multi-level hierarchy, enrollment management
- **API Prefix**: `/api/v1/schools/`, `/api/v1/courses/`

### Assignments
- **Purpose**: Assignment CRUD, extraction, publishing
- **Key Models**: Assignment (with 50+ fields for AI data)
- **Key Features**: PDF/image extraction via AI, question validation, rubric generation
- **Background Tasks**: extract_assignment_background_task
- **API Prefix**: `/api/v1/assignments/`

### Students
- **Purpose**: Student submissions and answer tracking
- **Key Models**: StudentSubmission (with ai_score, ai_feedback fields)
- **Key Features**: Answer extraction, submission tracking, dual grading (AI + human)
- **Background Tasks**: grade_submission_background_task
- **API Prefix**: `/api/v1/submissions/`

### Grading
- **Purpose**: Placeholder for future grading logic (currently minimal)
- **Key Models**: None yet (logic in ai_processor)
- **Future**: Dedicated rubric application, feedback generation

### AI Processor
- **Purpose**: AI integration, OCR, document processing
- **Key Components**:
  - `AIProcessor` class (OpenAI/OpenRouter client)
  - 14+ prompt templates (extraction, grading, generation, analytics)
  - `PDFService` (PDF text extraction via fitz)
- **Key Methods**: extract_assignment(), grade_assignment(), generate_assignment()

### Billing
- **Purpose**: Credit system, subscription management, usage tracking
- **Key Models**:
  - CreditWallet, CreditBucket, CreditLedger, CreditUsageLog
  - SubscriptionPlan (STANDARD, PRO, POWER, BETA)
  - UserSubscription, BetaProfile
- **Key Services**: SubscriptionService, AnalyticsService
- **API Prefix**: `/api/v1/billing/`

### Dashboard
- **Purpose**: Role-specific analytics and institutional insights
- **Key Methods**: get_platform_health(), get_adoption_metrics(), get_school_analytics()
- **Accessible By**: SuperAdmin (platform), SchoolAdmin (school), Teacher (course)
- **API Prefix**: `/api/v1/dashboard/`

### OCR Processor
- **Purpose**: Optical character recognition and text extraction
- **Status**: Placeholder (PaddleOCR integration ready)

---

## 🔑 Core Concepts

### Assignment Extraction
**What:** Convert PDFs/images to structured JSON assignments
**How:** Vision model (GPT-4V) + text extraction (fitz) → Prompt → JSON parsing
**Output:** Questions with rubrics, model answers, Bloom's levels
**Confidence:** 0-100 score indicating extraction quality
**Cost:** 1 credit per extraction

### Student Submission & AI Grading
**What:** Grade student submissions using AI
**How:** Extract answers → Load rubric → For each Q: AI evaluation → Aggregate score → Generate feedback
**Output:** ai_score, ai_feedback (per question), grading_confidence
**Cost:** 0.5 credit per submission

### Credit System
**What:** Token-based billing system (10M tokens in beta = ~10 credits)
**How:** FIFO bucket consumption, monthly renewal, carry-over with expiry
**Plans:** BETA (10M free), STANDARD (100K/mo), PRO (500K/mo), POWER (2M/mo)
**Actions:** Extract (1 credit), Grade (0.5 credit), Create (1.5 credits)

### Role-Based Access Control
```
SUPER_ADMIN → Full platform access
│
├─ SCHOOL_ADMIN → School data + teacher management
│
├─ TEACHER → Own courses/assignments
│
└─ STUDENT → Enrolled courses only
```

---

## 📊 Key Database Models (Quick Reference)

### CustomUser
```python
id (UUID) | email (unique) | user_type | school | is_active | password_hash
```

### Course
```python
id (UUID) | teacher (FK) | session (FK) | name | description | is_active
Unique: (name, teacher, session)
```

### Assignment
```python
id (UUID) | course (FK) | topic (FK) | title | raw_input | questions (JSON)
| total_points | assignment_type | extraction_confidence | status
```

### StudentSubmission
```python
id (UUID) | assignment (FK) | student (FK) | answers (JSON) | ai_score
| ai_feedback (JSON) | grading_confidence | submission_date
Unique: (student, assignment)
```

### CreditWallet
```python
id (UUID) | user (FK, unique) | total_available (computed from buckets)
```

### CreditBucket
```python
id (UUID) | wallet (FK) | bucket_type (MONTHLY/CARRY_OVER/PROMO/BETA)
| total_credits | used_credits | remaining (computed) | expires_at
```

### CreditLedger (immutable)
```python
id (UUID) | user (FK) | action_type | credits_changed | reason | balance_after | created_at
```

### UserSubscription
```python
id (UUID) | user (FK) | plan (FK) | billing_cycle_start/end | is_active
| auto_renew | status | cancellation_reason
```

---

## 🔄 Common API Flows

### Flow 1: Create & Extract Assignment
```
1. POST /api/v1/assignments/
   {course_id, title, raw_input (PDF/HTML)}

2. Response: {id: uuid, status: "DRAFT"}

3. Backend: Queue extract_assignment_background_task

4. Celery: Call AIProcessor.extract_assignment()

5. Poll GET /api/v1/assignments/{id}/
   → Returns: {status: "PUBLISHED", questions: [...], confidence: 87}
```

### Flow 2: Student Submits & Gets AI Grade
```
1. POST /api/v1/assignments/{id}/submit/
   {student_id, answers: {1: "answer", 2: "answer"}}

2. Response: {id: submission_uuid, status: "SUBMITTED"}

3. Backend: Queue grade_submission_background_task

4. Celery: Call AIProcessor.grade_assignment()
   for each question → aggregate score → format feedback

5. Poll GET /api/v1/submissions/{id}/
   → Returns: {ai_score: 85, ai_feedback: {...}, confidence: 92}
```

### Flow 3: Subscribe to Paid Plan
```
1. GET /api/v1/billing/plans/
   → Returns all subscription plans with features

2. POST /api/v1/billing/subscribe/
   {plan: "PRO"}

3. Backend: SubscriptionService.activate_subscription()
   - Create UserSubscription
   - Create new MONTHLY CreditBucket (500K credits)
   - Calculate rollover from unused beta credits
   - Deactivate old subscription

4. Response: {user_subscription_id, credits_available: 625000}
```

---

## 🛠️ Utility Commands

### Local Development Setup
```bash
# Clone & setup
git clone <repo>
cd Grade-Automator-Plus
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Create .env file
cp .example.env .env
# Edit .env with your secrets

# Run migrations
python manage.py migrate

# Create superuser
python manage.py createsuperuser

# Start dev server
python manage.py runserver

# In another terminal: start Celery
celery -A AutoGrader worker -l info

# In third terminal: start Celery Beat
celery -A AutoGrader beat -l info

# Monitor tasks (optional)
celery -A AutoGrader events flower
```

### Docker Deployment
```bash
# Build image
docker build -t grade-automator:latest .

# Run container
docker run -p 8000:8000 \
  -e DATABASE_URL="postgres://..." \
  -e SECRET_KEY="..." \
  -e OPENROUTER_API_KEY="..." \
  grade-automator:latest

# Run migrations inside container
docker exec <container_id> python manage.py migrate
```

### Database Queries
```bash
# Enter Django shell
python manage.py shell

# Common queries
from users.models import CustomUser
from assignments.models import Assignment

# Get user by email
user = CustomUser.objects.get(email='teacher@school.com')

# Get assignments for a course
assignments = Assignment.objects.filter(course_id=course_uuid)

# Get submissions for assignment
submissions = StudentSubmission.objects.filter(assignment_id=assignment_uuid)

# Check user credit balance
from billing.models import CreditWallet
wallet = CreditWallet.objects.get(user=user)
available = wallet.buckets.aggregate(
    total=Sum('total_credits') - Sum('used_credits')
)['total']
```

---

## 📝 File Structure (Important Files Only)

```
Grade-Automator-Plus/
├── manage.py                          (Django management)
├── requirements.txt                   (Python dependencies)
├── Dockerfile                         (Container config)
├── pyproject.toml                     (Project metadata)
│
├── AutoGrader/
│   ├── settings.py                    (Django config, Celery, Logging)
│   ├── urls.py                        (API routing)
│   ├── celery.py                      (Celery app + Beat schedule)
│   ├── asgi.py                        (Uvicorn)
│   └── wsgi.py                        (Gunicorn)
│
├── users/
│   ├── models.py                      (CustomUser)
│   ├── views.py                       (Auth endpoints)
│   ├── serializers.py                 (User validation)
│   ├── permissions.py                 (RBAC)
│   ├── services.py                    (OTP, email)
│   ├── middleware.py                  (Activity logging)
│   └── exceptions.py                  (Custom exception handler)
│
├── classrooms/
│   ├── models.py                      (School, Session, Course, Topic)
│   └── views.py                       (Organization endpoints)
│
├── assignments/
│   ├── models.py                      (Assignment)
│   ├── views.py                       (Assignment CRUD)
│   ├── services.py                    (PDFService, business logic)
│   ├── tasks.py                       (Celery: extract_assignment)
│   └── signals.py                     (Auto-trigger extraction)
│
├── students/
│   ├── models.py                      (StudentSubmission)
│   ├── views.py                       (Submission endpoints)
│   └── tasks.py                       (Celery: grade_submission)
│
├── ai_processor/
│   ├── services.py                    (AIProcessor class)
│   ├── validators.py                  (JSON validation)
│   ├── tools.py                       (Vision encoding, search)
│   ├── ASSIGNMENT_EXTRACTION_PROMPT_4_PROSE.txt
│   ├── ANSWERS_EXTRACTION_PROMPT_HTML_4.txt
│   ├── GRADING_ASSIGNMENT_PROMPT_2.txt
│   ├── GRADE_FORMATTER.txt
│   └── ...other prompts...
│
├── grading/
│   └── views.py                       (Placeholder for grading endpoints)
│
├── billing/
│   ├── models.py                      (Wallet, Bucket, Ledger, Plans, etc.)
│   ├── services.py                    (SubscriptionService, Analytics)
│   ├── views.py                       (Billing endpoints)
│   ├── tasks.py                       (Celery: monthly renewal, etc.)
│   └── errors.py                      (InsufficientCreditsError)
│
├── dashboard/
│   ├── views.py                       (Analytics endpoints)
│   ├── services.py                    (DashboardService)
│   └── tasks.py                       (Compute health metrics)
│
├── docs/
│   └── tasks.md                       (Development tasks)
│
└── templates/
    └── (Django HTML templates, if any)
```

---

## 🚨 Important Settings in settings.py

```python
# Environment
ENVIRONMENT = env('ENVIRONMENT')  # local|dev|prod
DEBUG = ENVIRONMENT == 'local'

# Database
DATABASES = {
    'default': dj_database_url.config(
        conn_max_age=600,
        conn_health_checks=True,
    )
}

# JWT tokens
SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=15),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    ...
}

# Celery
CELERY_BROKER_URL = 'redis://localhost:6379/0'
CELERY_RESULT_BACKEND = 'redis://localhost:6379/0'
CELERY_BEAT_SCHEDULE = {
    'monthly-credit-renewal': {...},
    'beta-usage-snapshot': {...},
}

# Logging
LOGGING = {
    'version': 1,
    'handlers': {
        'console': {'class': 'logging.StreamHandler'},
    },
    ...
}
```

---

## 🔗 API Base URL
```
http://localhost:8000/api/v1/
```

### Interactive Documentation
```
Swagger UI:  http://localhost:8000/api/v1/swagger-ui/
ReDoc:       http://localhost:8000/api/v1/redoc/
OpenAPI:     http://localhost:8000/api/v1/schema/
```

---

## 📊 Billing Tiers Cheat Sheet

| Tier | Monthly Credits | Rollover | Max Rollover | Block Size | Price/Block |
|------|---|---|---|---|---|
| **BETA** | 10M tokens | - | - | - | Free |
| **STANDARD** | 100K | 50% | 50K | 10K | $2.50 |
| **PRO** | 500K | 75% | 100K | 50K | $2.00 |
| **POWER** | 2M | 100% | 250K | 100K | $1.80 |

---

## ⏱️ Celery Beat Tasks (Scheduled)

| Task | Schedule | What It Does |
|------|----------|-------------|
| monthly-credit-renewal | 1st, 00:00 UTC | Create new monthly buckets, calculate rollover |
| beta-usage-snapshot | Daily, 00:00 UTC | Snapshot token usage for beta cohorts |
| platform-health-metrics | Every hour | Aggregate KPIs for dashboard |
| adoption-metrics-update | Every 6 hours | School adoption signals |
| process-overage-charges | Daily, 02:00 UTC | Auto-charge overage blocks (if enabled) |

---

## 🔐 Authentication Headers

```
# Login
POST /api/v1/auth/login
Content-Type: application/json

{
  "email": "teacher@school.com",
  "password": "password123"  # pragma: allowlist secret
}

# Response
{
  "access": "eyJ0eXAiOiJKV1QiLCJhbGc...",
  "refresh": "eyJ0eXAiOiJKV1QiLCJhbGc..."
}

# Subsequent requests
Authorization: Bearer eyJ0eXAiOiJKV1QiLCJhbGc...

# Refresh token
POST /api/v1/auth/refresh
Content-Type: application/json

{
  "refresh": "eyJ0eXAiOiJKV1QiLCJhbGc..."
}
```

---

## 🐛 Debugging Tips

### Check Celery Tasks
```bash
# List active tasks
celery -A AutoGrader inspect active

# Get task status
celery -A AutoGrader inspect registered

# Check Flower dashboard
http://localhost:5555
```

### Database Queries
```bash
# Enable SQL logging in Django shell
from django.db import connection
connection.queries_log.enable()

# Then run query
user = CustomUser.objects.get(email='test@test.com')

# Print last query
print(connection.queries[-1])
```

### Check Redis
```bash
# Connect to Redis
redis-cli

# See all keys
keys *

# Check Celery queue
LRANGE celery 0 -1

# Monitor live
MONITOR
```

### Django Shell Shortcuts
```bash
python manage.py shell

# Quick imports
from users.models import CustomUser
from assignments.models import Assignment
from students.models import StudentSubmission
from billing.models import CreditWallet, SubscriptionPlan

# Check user credits
user = CustomUser.objects.get(email='teacher@school.com')
wallet = CreditWallet.objects.get(user=user)
print(f"Available: {wallet.total_available_credits}")

# Check assignments
assignments = Assignment.objects.filter(status='PUBLISHED')
print(f"Published: {assignments.count()}")

# Check submissions
subs = StudentSubmission.objects.filter(ai_graded_at__isnull=False)
print(f"Graded: {subs.count()}")
```

---

## 📚 External Documentation Links

- **Django**: https://docs.djangoproject.com/en/5.2/
- **DRF**: https://www.django-rest-framework.org/
- **Celery**: https://docs.celeryproject.org/
- **OpenAI API**: https://platform.openai.com/docs/
- **OpenRouter**: https://openrouter.ai/docs
- **PostgreSQL**: https://www.postgresql.org/docs/

---

## 🎯 Next Steps for Development

1. **Setup local environment** following "Local Development Setup"
2. **Read** `PROJECT_ARCHITECTURE.md` for comprehensive understanding
3. **Review** `SYSTEM_DIAGRAMS.md` for visual flows
4. **Check** `/docs/tasks.md` for active development items
5. **Review** `BRANCHING_STRATEGY.md` before committing code

---

**Quick Links:**
- **Main Docs**: `PROJECT_ARCHITECTURE.md`
- **Visual Diagrams**: `SYSTEM_DIAGRAMS.md`
- **Branching Guide**: `BRANCHING_STRATEGY.md`
- **API Docs (Live)**: `/api/v1/swagger-ui/`

**Last Updated**: March 30, 2026
**Version**: 1.0 (Production-Ready)
