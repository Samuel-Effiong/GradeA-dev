# H-267 notes (Hardening Engineer, 4d)

- **The row** (batch 30): restrict `Image.open` to the four allowed formats (JPEG, PNG,
  GIF, WEBP) via Pillow's `formats=` parameter, and add the same restriction to profile
  image uploads.
- **Base:** H-263's gated tip `aede70b4` (Senior Manager).
- **Branch** `task/h267-pillow-formats`.

## What changed

### 1. Upload image chokepoint — `_compress_uploaded_image`

`assignments/services.py:549`: `Image.open` now receives
`formats=_ALLOWED_PILLOW_NAMES`, restricting Pillow to JPEG, PNG, GIF and WEBP decoders
only. `_ALLOWED_PILLOW_NAMES` is imported from the single source of truth
`AutoGrader/image_formats.py`. A body in any other format (PSD, BMP, TIFF, ICO, EPS,
etc.) triggers `UnidentifiedImageError`, which the existing `except Exception` clause maps
to `FileUnreadableError` (422, reason code FILE_UNREADABLE).

`PILLOW_FORMATS` (MIME → Pillow name mapping) and `_ALLOWED_PILLOW_NAMES` (the tuple of
Pillow names) live in `AutoGrader/image_formats.py`. `IMAGE_FORMATS` stays on the service
class at line 515. A test (`test_allowed_formats_match_image_formats`) asserts that the
mapping's keys equal `IMAGE_FORMATS` exactly, so the two lists cannot drift apart.

**Every user-uploaded image** reaches `_compress_uploaded_image` via `prepare_ai_content`:
- Student single/async/batch upload (`students/views.py:606`, `:719`, `:1497`)
- Teacher batch assignment upload (`assignments/views.py:1161`)
- Assignment file upload (`assignments/file_uploads.py:116`)
- Upload door estimate (`assignments/upload_door.py:66`)
- Benchmark live/harness (`ai_processor/benchmark/answers/live.py:176`, `harness.py:119`)

### 2. Profile image uploads

Three DRF serializer `ImageField` declarations and the Django admin form now validate
that the uploaded image is one of the four allowed formats via the shared
`validate_image_format` validator in `AutoGrader/image_formats.py`.

| Serializer / Form | Field | File:line |
|---|---|---|
| `DirectAddStudentSerializer` | `profile_image` | `classrooms/serializers.py:658` |
| `StudentRegistrationCompletionSerializer` | `profile_image` | `classrooms/serializers.py:834` |
| `SchoolWithAdminSerializer` | `admin_profile_image` | `classrooms/serializers.py:1021` |
| `CustomUserSerializer` | `profile_image` (via `extra_kwargs`) | `users/serializers.py:133` |
| `_ProfileImageForm` (admin) | `profile_image` | `users/admin.py:28` |

The validator opens the file with `Image.open(formats=_ALLOWED_PILLOW_NAMES)` and
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

## Gate pack (written before any gate run)

### Consolidation (d9218233)

`assignments/validators.py` deleted. `PILLOW_FORMATS`, `_ALLOWED_PILLOW_NAMES`, and
`validate_image_format` now live in `AutoGrader/image_formats.py` — the single source of
truth. All consumers import from there.

Gap-closing tests (144e0015): 6 new tests close all 5 mutant gaps (M4–M7 write-path
validators, M9 verify()). Total: 19 tests in 3 classes.

### Seam check

Only these runtime units change between base `aede70b4` and tip:

| File | Change |
|---|---|
| `AutoGrader/image_formats.py` | NEW — format mapping, tuple, validator |
| `assignments/services.py` | `formats=_ALLOWED_PILLOW_NAMES` on `Image.open` (+import) |
| `classrooms/serializers.py` | `validators=[validate_image_format]` on 3 ImageFields (+import) |
| `users/serializers.py` | `validators: [validate_image_format]` in extra_kwargs (+import) |
| `users/admin.py` | `_ProfileImageForm` with `clean_profile_image` (+import) |

No other runtime files touched. `assignments/tests_h267_pillow_formats.py` is test-only.

Expected AST seam: `git diff --name-only aede70b4..HEAD -- '*.py' ':(exclude)docs/*'
':(exclude)*tests*'` yields exactly the 5 files above.

### Red run — 19 tests at the tip, call-site files reverted to base `aede70b4`

Method: keep `AutoGrader/image_formats.py` and `assignments/tests_h267_pillow_formats.py`
from the tip. Revert the 5 call-site files to base:
`assignments/services.py`, `classrooms/serializers.py`, `users/serializers.py`,
`users/admin.py`. This isolates the wiring: the module and validator exist, but nothing
uses them yet.

**Expected result:** Ran 19, Failures 7, Errors 1.

| # | Test | Expected | Reason |
|---|---|---|---|
| 1 | `test_psd_labelled_as_png_is_refused` | FAIL | No `formats=` at base; PSD opens and compresses to JPEG; `FileUnreadableError` not raised |
| 2 | `test_bmp_labelled_as_png_is_refused` | FAIL | Same; BMP opens fine without format restriction |
| 3 | `test_tiff_labelled_as_png_is_refused` | FAIL | Same; TIFF opens fine |
| 4 | `test_jpeg_labelled_correctly_is_accepted` | GREEN | JPEG accepted at base — no false positive |
| 5 | `test_png_labelled_correctly_is_accepted` | GREEN | PNG accepted at base |
| 6 | `test_gif_labelled_correctly_is_accepted` | GREEN | GIF accepted at base |
| 7 | `test_webp_labelled_correctly_is_accepted` | GREEN | WEBP accepted at base |
| 8 | `test_allowed_formats_match_image_formats` | GREEN | `PILLOW_FORMATS` from tip's `image_formats.py`; `IMAGE_FORMATS` on the service class is unchanged at base; keys match |
| 9 | `test_pillow_formats_values_are_uppercase_strings` | GREEN | `PILLOW_FORMATS` from tip; values are uppercase |
| 10 | `test_validator_accepts_exactly_the_four_allowed_formats` | GREEN | Validator from tip; all 4 formats are valid |
| 11 | `test_validator_rejects_bmp` | GREEN | Validator from tip rejects BMP |
| 12 | `test_validator_rejects_corrupt_png` | GREEN | Validator from tip; `verify()` catches CRC mismatch |
| 13 | `test_direct_add_student_rejects_bmp` | FAIL | No validator on `DirectAddStudentSerializer` at base; BMP accepted by bare `ImageField` |
| 14 | `test_direct_add_student_accepts_jpeg` | GREEN | JPEG accepted by bare `ImageField` at base |
| 15 | `test_student_registration_completion_rejects_bmp` | FAIL | No validator at base; BMP accepted |
| 16 | `test_student_registration_completion_accepts_png` | GREEN | PNG accepted at base |
| 17 | `test_school_with_admin_rejects_bmp` | FAIL | No validator on `admin_profile_image` at base; BMP accepted |
| 18 | `test_custom_user_serializer_rejects_bmp` | FAIL | No validator in `extra_kwargs` at base; BMP accepted |
| 19 | `test_admin_form_rejects_bmp` | ERROR | `_ProfileImageForm` does not exist at base; `ImportError` on `from users.admin import _ProfileImageForm` |

Tests 4–7, 8–12, 14, 16 are green because: the upload path already accepts those four
formats (no false positives); the `image_formats.py` module itself is kept from the tip
so its own unit tests pass; and bare `ImageField` accepts JPEG/PNG without a validator.

The 7 FAILs prove that each wiring point (3 upload restrictions + 4 serializer validators)
is absent at base. The 1 ERROR proves the admin form class is new.

### Mutant battery (9 mutants, all KILLED)

| # | Mutant | Killing test |
|---|---|---|
| M1 | Drop `formats=_ALLOWED_PILLOW_NAMES` at `services.py:549` | `test_psd_labelled_as_png_is_refused` |
| M2 | Remove `"image/webp": "WEBP"` from `PILLOW_FORMATS` | `test_allowed_formats_match_image_formats`; also `test_validator_accepts_exactly_the_four_allowed_formats` |
| M3 | Drop `validators=[validate_image_format]` from `DirectAddStudentSerializer.profile_image` (`:658`) | `test_direct_add_student_rejects_bmp` |
| M4 | Drop `validators=[validate_image_format]` from `StudentRegistrationCompletionSerializer.profile_image` (`:834`) | `test_student_registration_completion_rejects_bmp` |
| M5 | Drop `validators=[validate_image_format]` from `SchoolWithAdminSerializer.admin_profile_image` (`:1021`) | `test_school_with_admin_rejects_bmp` |
| M6 | Drop `validators=[validate_image_format]` from `CustomUserSerializer` extra_kwargs (`:133`) | `test_custom_user_serializer_rejects_bmp` |
| M7 | Drop `validate_image_format(img)` from `_ProfileImageForm.clean_profile_image` (`admin.py:28`) | `test_admin_form_rejects_bmp` |
| M8 | Validator swallows exception: `return` instead of `raise ValidationError(...)` | `test_validator_rejects_bmp` |
| M9 | Remove `img.verify()` from `validate_image_format` | `test_validator_rejects_corrupt_png` (flipped IDAT byte, CRC mismatch) |

### Near test suites

| App | Modules | Scope |
|---|---|---|
| `assignments` | `assignments.tests`, `assignments.tests_h267_pillow_formats`, plus all `assignments.tests_*` | Changed `services.py` |
| `classrooms` | `classrooms.tests`, `classrooms.test_serializers`, `classrooms.test_views`, plus all `classrooms.tests_*` | Changed `serializers.py` |
| `users` | `users.tests`, `users.tests_models_and_admin`, plus all `users.tests_*` | Changed `serializers.py`, `admin.py` |

Guard modules (imports only, no code change): none beyond the three apps above.

### Private venv

Build from `aede70b4` requirements (which carry the H-263 upgrades: Pillow ≥ 11.2.1,
djangorestframework ≥ 3.16.0, gunicorn ≥ 23.0.0, PyJWT ≥ 2.10.1, lxml ≥ 5.3.1,
urllib3 ≥ 2.3.0). Use `requirements.txt` at the tip (identical pins). Create a private
venv, not the shared `AutoGrader_env`.

### Gate runner plan

1. Private venv from tip's `requirements.txt`.
2. `makemigrations --check` — must be clean (no model changes).
3. AST seam check: `git diff --name-only aede70b4..HEAD -- '*.py' ':(exclude)docs/*' ':(exclude)*tests*'` must yield exactly the 5 files listed above.
4. Red run: checkout base versions of the 5 call-site files, keep `AutoGrader/image_formats.py` and tests from tip, run 19 H-267 tests → Ran 19, Failures 7, Errors 1. Restore tip files after.
5. Green run: 19 H-267 tests at the tip → Ran 19, OK.
6. Mutant plants A1–A9 (all 9 mutants killed).
7. Near suites: assignments, classrooms, users.
8. Full suite in private venv under `systemd-run --scope -p MemoryMax=8G`.
