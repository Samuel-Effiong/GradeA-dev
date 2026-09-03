# License Subscription API Documentation

**Status**: ✅ PRODUCTION-READY
**Date**: 2026-06-09
**Version**: 1.0.0

---

## Overview

The License Subscription API provides RESTful endpoints for managing institutional (License) subscriptions. This API allows schools to:
- Create and manage institutional licenses for multiple teachers
- Enroll/remove teachers from licenses
- View credit allocations
- Monitor renewal schedules
- Manage billing cycles

---

## Base URL

```
/api/billing/
```

All endpoints require authentication via JWT token in the `Authorization` header.

---

## Authentication

All endpoints require a valid JWT token:

```
Authorization: Bearer <token>
```

---

## Endpoints Summary

| Method | Endpoint | Description | Permissions |
|--------|----------|-------------|-------------|
| **GET** | `/license-subscriptions/` | List all license subscriptions | School Admin, Super Admin |
| **POST** | `/license-subscriptions/` | Create new license subscription | School Admin, Super Admin |
| **GET** | `/license-subscriptions/{id}/` | Get license subscription details | School Admin, Super Admin |
| **PATCH** | `/license-subscriptions/{id}/` | Update license subscription | School Admin, Super Admin |
| **POST** | `/license-subscriptions/{id}/cancel/` | Cancel license subscription | Super Admin only |
| **POST** | `/license-subscriptions/{id}/add-teachers/` | Enroll teachers to license | School Admin, Super Admin |
| **POST** | `/license-subscriptions/{id}/remove-teachers/` | Remove teachers from license | School Admin, Super Admin |
| **POST** | `/license-subscriptions/{id}/process-renewal/` | Manually trigger renewal | Super Admin only |
| **GET** | `/license-subscriptions/{id}/renewal-info/` | Get renewal status | School Admin, Super Admin |
| **GET** | `/school-credit-allocations/` | List credit allocations | Teachers, School Admin, Super Admin |
| **GET** | `/school-credit-allocations/{id}/` | Get allocation details | Teachers, School Admin, Super Admin |

---

## Detailed Endpoint Documentation

### 1. List License Subscriptions

```http
GET /api/billing/license-subscriptions/
```

**Description**: Get list of all license subscriptions.
- Super Admins see all licenses
- School Admins see only licenses for their school(s)

**Query Parameters**:
- `school` (int) - Filter by school ID
- `is_active` (bool) - Filter by active status
- `auto_renew` (bool) - Filter by auto-renew setting
- `search` (string) - Search in school name, plan name, admin email
- `ordering` (string) - Order by field (e.g., `-created_at`, `billing_cycle_end`)

**Example Request**:
```bash
curl -X GET \
  "http://localhost:8000/api/billing/license-subscriptions/?is_active=true&ordering=-created_at" \
  -H "Authorization: Bearer <token>"
```

**Example Response** (200 OK):
```json
{
  "count": 2,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440000",
      "school": 1,
      "school_name": "Jefferson High School",
      "admin_user": 42,
      "admin_email": "principal@jefferson.edu",
      "plan": 5,
      "plan_name": "Pro Plan",
      "plan_category": "LICENSE",
      "monthly_credits": 50000,
      "display_monthly_credits": 50,
      "billing_cycle_start": "2026-06-01T00:00:00Z",
      "billing_cycle_end": "2026-07-01T00:00:00Z",
      "is_active": true,
      "auto_renew": true,
      "stripe_subscription_id": "sub_123456789",
      "teacher_count": 5,
      "active_teacher_count": 5,
      "created_at": "2026-06-09T10:30:00Z",
      "updated_at": "2026-06-09T10:30:00Z",
      "allocations": [...]
    }
  ]
}
```

---

### 2. Create License Subscription

```http
POST /api/billing/license-subscriptions/
```

**Description**: Create a new institutional license subscription.

**Required Fields**:
- `school` (int) - School ID
- `admin_user` (int) - User ID of school admin managing the license
- `plan` (int) - Subscription plan ID (must be LICENSE category)

**Optional Fields**:
- `teacher_ids` (array of int) - Teacher IDs to enroll immediately

**Example Request**:
```bash
curl -X POST \
  "http://localhost:8000/api/billing/license-subscriptions/" \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "school": 1,
    "admin_user": 42,
    "plan": 5,
    "teacher_ids": [101, 102, 103]
  }'
```

**Example Response** (201 CREATED):
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "school": 1,
  "school_name": "Jefferson High School",
  "admin_user": 42,
  "admin_email": "principal@jefferson.edu",
  "plan": 5,
  "plan_name": "Pro Plan",
  "plan_category": "LICENSE",
  "monthly_credits": 50000,
  "display_monthly_credits": 50,
  "billing_cycle_start": "2026-06-09T10:30:00Z",
  "billing_cycle_end": "2026-07-09T10:30:00Z",
  "is_active": true,
  "auto_renew": true,
  "stripe_subscription_id": null,
  "teacher_count": 3,
  "active_teacher_count": 3,
  "created_at": "2026-06-09T10:30:00Z",
  "updated_at": "2026-06-09T10:30:00Z",
  "allocations": [
    {
      "id": "660e8400-e29b-41d4-a716-446655440001",
      "license_subscription": "550e8400-e29b-41d4-a716-446655440000",
      "user": 101,
      "user_email": "teacher1@jefferson.edu",
      "user_full_name": "John Smith",
      "monthly_allocation": 50000,
      "display_monthly_allocation": 50,
      "license_school_name": "Jefferson High School",
      "license_plan_name": "Pro Plan",
      "is_active": true,
      "created_at": "2026-06-09T10:30:00Z",
      "updated_at": "2026-06-09T10:30:00Z"
    }
  ]
}
```

**Error Responses**:
- `400 BAD REQUEST` - Missing required fields or invalid data
- `403 FORBIDDEN` - Not authorized to create license for this school
- `404 NOT FOUND` - School, admin user, or plan not found

---

### 3. Get License Subscription Details

```http
GET /api/billing/license-subscriptions/{id}/
```

**Description**: Get detailed information about a specific license subscription.

**Example Request**:
```bash
curl -X GET \
  "http://localhost:8000/api/billing/license-subscriptions/550e8400-e29b-41d4-a716-446655440000/" \
  -H "Authorization: Bearer <token>"
```

**Example Response** (200 OK): Same as create response

**Error Responses**:
- `403 FORBIDDEN` - Not authorized to view this license
- `404 NOT FOUND` - License subscription not found

---

### 4. Update License Subscription

```http
PATCH /api/billing/license-subscriptions/{id}/
```

**Description**: Update license subscription settings.

**Updatable Fields**:
- `auto_renew` (bool) - Enable/disable auto-renewal
- `custom_price_cents` (int, nullable) - Override price for this license

`is_active` is read-only here — use `POST .../cancel/` to deactivate a
license (it correctly stops a STRIPE subscription from renewing first).

**Example Request**:
```bash
curl -X PATCH \
  "http://localhost:8000/api/billing/license-subscriptions/550e8400-e29b-41d4-a716-446655440000/" \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "auto_renew": false
  }'
```

**Example Response** (200 OK): Updated license object

---

### 5. Cancel License Subscription

```http
DELETE /api/billing/license-subscriptions/{id}/
```

**Description**: Cancel/delete a license subscription.

**Example Request**:
```bash
curl -X DELETE \
  "http://localhost:8000/api/billing/license-subscriptions/550e8400-e29b-41d4-a716-446655440000/" \
  -H "Authorization: Bearer <token>"
```

**Response** (204 NO CONTENT)

---

### 6. Add Teachers to License

```http
POST /api/billing/license-subscriptions/{id}/add-teachers/
```

**Description**: Enroll one or more teachers to a license subscription.

**Required Fields**:
- `teacher_ids` (array of int) - Teacher user IDs to enroll

**Example Request**:
```bash
curl -X POST \
  "http://localhost:8000/api/billing/license-subscriptions/550e8400-e29b-41d4-a716-446655440000/add-teachers/" \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "teacher_ids": [104, 105]
  }'
```

**Example Response** (200 OK):
```json
{
  "successful": 2,
  "failed": 0,
  "errors": []
}
```

**Partial Success Response** (200 OK):
```json
{
  "successful": 1,
  "failed": 1,
  "errors": [
    {
      "teacher_id": 105,
      "error": "User not found"
    }
  ]
}
```

---

### 7. Remove Teachers from License

```http
POST /api/billing/license-subscriptions/{id}/remove-teachers/
```

**Description**: Remove one or more teachers from a license subscription.

**Required Fields**:
- `teacher_ids` (array of int) - Teacher user IDs to remove

**Example Request**:
```bash
curl -X POST \
  "http://localhost:8000/api/billing/license-subscriptions/550e8400-e29b-41d4-a716-446655440000/remove-teachers/" \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "teacher_ids": [104, 105]
  }'
```

**Example Response** (200 OK):
```json
{
  "successful": 2,
  "failed": 0,
  "errors": []
}
```

---

### 8. Get Renewal Information

```http
GET /api/billing/license-subscriptions/{id}/renewal-info/
```

**Description**: Get information about the next renewal cycle.

**Example Request**:
```bash
curl -X GET \
  "http://localhost:8000/api/billing/license-subscriptions/550e8400-e29b-41d4-a716-446655440000/renewal-info/" \
  -H "Authorization: Bearer <token>"
```

**Example Response** (200 OK):
```json
{
  "next_renewal_date": "2026-07-09T10:30:00Z",
  "days_until_renewal": 30,
  "auto_renew": true,
  "is_active": true,
  "teacher_count": 5,
  "active_teacher_count": 5,
  "allocations": [
    {
      "id": "660e8400-e29b-41d4-a716-446655440001",
      "user_email": "teacher1@jefferson.edu",
      "monthly_allocation": 50000,
      "display_monthly_allocation": 50,
      "is_active": true
    }
  ]
}
```

---

### 9. Process Manual Renewal

```http
POST /api/billing/license-subscriptions/{id}/process-renewal/
```

**Description**: Manually trigger license renewal. **Super Admin only**.

**Example Request**:
```bash
curl -X POST \
  "http://localhost:8000/api/billing/license-subscriptions/550e8400-e29b-41d4-a716-446655440000/process-renewal/" \
  -H "Authorization: Bearer <token>"
```

**Example Response** (200 OK):
```json
{
  "status": "Renewal processed successfully"
}
```

---

### 10. List Credit Allocations

```http
GET /api/billing/school-credit-allocations/
```

**Description**: Get list of credit allocations.
- Teachers see only their own allocation
- School Admins see allocations for their school
- Super Admins see all allocations

**Query Parameters**:
- `license_subscription` (UUID) - Filter by license
- `is_active` (bool) - Filter by active status
- `search` (string) - Search in user email or school name
- `ordering` (string) - Order by field

**Example Request**:
```bash
curl -X GET \
  "http://localhost:8000/api/billing/school-credit-allocations/?is_active=true" \
  -H "Authorization: Bearer <token>"
```

**Example Response** (200 OK):
```json
{
  "count": 5,
  "results": [
    {
      "id": "660e8400-e29b-41d4-a716-446655440001",
      "license_subscription": "550e8400-e29b-41d4-a716-446655440000",
      "user": 101,
      "user_email": "teacher1@jefferson.edu",
      "user_full_name": "John Smith",
      "monthly_allocation": 50000,
      "display_monthly_allocation": 50,
      "license_school_name": "Jefferson High School",
      "license_plan_name": "Pro Plan",
      "is_active": true,
      "created_at": "2026-06-09T10:30:00Z",
      "updated_at": "2026-06-09T10:30:00Z"
    }
  ]
}
```

---

### 11. Get Credit Allocation Details

```http
GET /api/billing/school-credit-allocations/{id}/
```

**Description**: Get detailed information about a specific credit allocation.

**Example Request**:
```bash
curl -X GET \
  "http://localhost:8000/api/billing/school-credit-allocations/660e8400-e29b-41d4-a716-446655440001/" \
  -H "Authorization: Bearer <token>"
```

**Example Response** (200 OK): Same as list item

---

## Permission Model

### LicenseSubscriptionViewSet
- **LIST / RETRIEVE**: School Admins (their school only), Super Admins (all)
- **CREATE / UPDATE / DELETE**: School Admins (their school only), Super Admins (all)
- **Custom Actions**: School Admins (their school only), Super Admins (all)

### SchoolCreditAllocationViewSet (Read-Only)
- **LIST**: Teachers (their own only), School Admins (their school only), Super Admins (all)
- **RETRIEVE**: Teachers (their own only), School Admins (their school only), Super Admins (all)

### Teachers
- Cannot modify license subscriptions (403 Forbidden)
- Can view their own credit allocation
- Cannot see other teachers' allocations
- Cannot see other schools' licenses

---

## Error Handling

All errors follow this format:

```json
{
  "error": "Error message describing what went wrong"
}
```

### HTTP Status Codes

| Status | Meaning |
|--------|---------|
| 200 | OK - Successful GET/PATCH/POST |
| 201 | Created - Successful POST creating new resource |
| 204 | No Content - Successful DELETE |
| 400 | Bad Request - Invalid parameters or data |
| 403 | Forbidden - Insufficient permissions |
| 404 | Not Found - Resource doesn't exist |
| 500 | Internal Server Error - Server error |

---

## Common Workflows

### Workflow 1: Create License and Enroll Teachers

```bash
# Step 1: Create license subscription
curl -X POST \
  "http://localhost:8000/api/billing/license-subscriptions/" \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "school": 1,
    "admin_user": 42,
    "plan": 5
  }'

# Response includes: license ID (550e8400...)

# Step 2: Add teachers to license
curl -X POST \
  "http://localhost:8000/api/billing/license-subscriptions/550e8400-e29b-41d4-a716-446655440000/add-teachers/" \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "teacher_ids": [101, 102, 103]
  }'
```

### Workflow 2: Check Renewal Status

```bash
curl -X GET \
  "http://localhost:8000/api/billing/license-subscriptions/550e8400-e29b-41d4-a716-446655440000/renewal-info/" \
  -H "Authorization: Bearer <token>"
```

### Workflow 3: Remove Teacher and Add New One

```bash
# Step 1: Remove teacher
curl -X POST \
  "http://localhost:8000/api/billing/license-subscriptions/550e8400-e29b-41d4-a716-446655440000/remove-teachers/" \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "teacher_ids": [104]
  }'

# Step 2: Add new teacher
curl -X POST \
  "http://localhost:8000/api/billing/license-subscriptions/550e8400-e29b-41d4-a716-446655440000/add-teachers/" \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "teacher_ids": [105]
  }'
```

---

## Best Practices

1. **Always validate inputs** before sending to API
2. **Handle batch operation errors gracefully** - Check failed count and errors array
3. **Cache renewal dates** to avoid repeated API calls
4. **Use filtering** to reduce data transfer
5. **Implement pagination** for large result sets (uses Django rest_framework defaults)
6. **Handle rate limiting** if deployed with rate limiting middleware
7. **Implement retry logic** for transient failures (503, etc.)

---

## Pagination

Default pagination is handled by Django REST Framework's DefaultPagination.

Each list response includes:
- `count` - Total number of results
- `next` - URL to next page (or null if no next page)
- `previous` - URL to previous page (or null if no previous page)
- `results` - Array of results

---

## Testing

Example test requests are provided in [POSTMAN_COLLECTION.json](./postman_collection.json)

---

## Support

For issues or questions about the API:
1. Check the error message in the response
2. Review this documentation
3. Check server logs for detailed errors
4. Contact the development team

---

**Document Version**: 1.0
**Last Updated**: 2026-06-09
