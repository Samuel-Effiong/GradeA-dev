# H-267 notes (Hardening Engineer, 4d)

- **The row** (batch 30): restrict `Image.open` to the four allowed formats (JPEG, PNG,
  GIF, WEBP) via Pillow's `formats=` parameter, and add the same restriction to profile
  image uploads.
- **Base:** H-263's gated tip `aede70b4` (Senior Manager).
- **Branch** `task/h267-pillow-formats`.

## What changed

### 1. Upload image chokepoint — `_compress_uploaded_image`

`assignments/services.py:551` (previously line 546): `Image.open` now receives
`formats=list(cls.PILLOW_FORMATS.values())`, restricting Pillow to JPEG, PNG, GIF and
WEBP decoders only. A body in any other format (PSD, BMP, TIFF, ICO, EPS, etc.) triggers
`UnidentifiedImageError`, which the existing `except Exception` clause maps to
`FileUnreadableError` (422, reason code FILE_UNREADABLE).

`PILLOW_FORMATS` is a dict mapping MIME types to Pillow format names, kept next to
`IMAGE_FORMATS` at line 515. A test (`test_allowed_formats_match_image_formats`) asserts
that the mapping's keys equal `IMAGE_FORMATS` exactly, so the two lists cannot drift apart.

**Every user-uploaded image** reaches `_compress_uploaded_image` via `prepare_ai_content`:
- Student single/async/batch upload (`students/views.py:606`, `:719`, `:1497`)
- Teacher batch assignment upload (`assignments/views.py:1161`)
- Assignment file upload (`assignments/file_uploads.py:116`)
- Upload door estimate (`assignments/upload_door.py:66`)
- Benchmark live/harness (`ai_processor/benchmark/answers/live.py:176`, `harness.py:119`)

### 2. Profile image uploads

Three DRF serializer `ImageField` declarations and the Django admin form now validate
that the uploaded image is one of the four allowed formats via a shared
`validate_image_format` validator (`assignments/validators.py`).

| Serializer / Form | Field | File:line |
|---|---|---|
| `DirectAddStudentSerializer` | `profile_image` | `classrooms/serializers.py:655` |
| `StudentRegistrationCompletionSerializer` | `profile_image` | `classrooms/serializers.py:828` |
| `SchoolWithAdminSerializer` | `admin_profile_image` | `classrooms/serializers.py:1012` |
| `CustomUserSerializer` | `profile_image` (via `extra_kwargs`) | `users/serializers.py:133` |
| `_ProfileImageForm` (admin) | `profile_image` | `users/admin.py:21` |

The validator opens the file with `Image.open(formats=ALLOWED_IMAGE_FORMATS)` and
`verify()`. If the format is not in the set, a clear `ValidationError` is raised:
"Upload a JPEG, PNG, GIF or WEBP image."

**Write routes for profile_image:**
1. `POST /api/v1/users/register/` — `CustomUserSerializer`
2. `PATCH /api/v1/users/{id}/` — `CustomUserSerializer`
3. `POST /api/v1/users/` (superadmin) — `CustomUserSerializer`
4. `POST /api/v1/course/{id}/direct-add-student/` — `DirectAddStudentSerializer`
5. `POST /api/v1/course/{id}/bulk-add-students/` — `BulkAddStudentSerializer` (wraps `DirectAddStudentSerializer`)
6. `POST /api/v1/schools/create_with_admin/` — `SchoolWithAdminSerializer`
7. Django admin — `_ProfileImageForm`

The closed student registration endpoint (`/auth/register/student`, returns 410 GONE,
H-152) still has `StudentRegistrationCompletionSerializer` guarded as well.

**Visible change:** profile pictures in formats other than JPEG, PNG, GIF or WEBP (such
as BMP or TIFF) will now be refused with a field error. The SM will inform the owner.

### 3. Image.open calls NOT on upload paths — no `formats=` needed

- `ai_processor/services.py:6106` — `PDFService.__extract_images_from_pdf()`: opens
  pages rendered by poppler's `convert_from_path`, always PNG. Trusted internal output.
- `ai_processor/services.py:6192` — `OCRService.get_image_dimensions()`: opens
  base64-decoded bytes that always come from `compress_image_for_upload`
  (`ai_processor/tools.py:333`), which saves as **JPEG** (`format="JPEG"`). No path can
  hand it raw upload bytes: `prepare_ai_content` always compresses first, and
  `PDFService.extract` also compresses each page through `compress_image_for_upload`.

## Red-run expectations (written before the code)

The test module `assignments.tests_h267_pillow_formats` has three test classes:

1. **UploadFormatRestrictionTests** (7 tests): PSD/BMP/TIFF labelled as image/png must
   raise `FileUnreadableError` with status 422. JPEG/PNG/GIF/WEBP must pass.
2. **FormatMappingTests** (2 tests): `PILLOW_FORMATS.keys()` must equal `IMAGE_FORMATS`;
   values must be uppercase.
3. **ProfileImageFormatTests** (2 tests): BMP on `DirectAddStudentSerializer` must be
   rejected as a field error; JPEG must be accepted.

Before the code: 4 failures (format restrictions not in place) + 2 errors
(`PILLOW_FORMATS` attribute doesn't exist). After: all 11 pass.
