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
truth. All consumers import from there. Two new tests added (total 13):
`test_validator_accepts_exactly_the_four_allowed_formats`,
`test_validator_rejects_bmp`.

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

### Red run — 13 tests at the tip, code at base `aede70b4`

At base, `AutoGrader/image_formats.py` does not exist. The test module's top-level import
`from AutoGrader.image_formats import PILLOW_FORMATS` raises `ImportError`. Django's test
runner cannot collect any test from the module.

**Expected result:** Ran 0, Errors 1 — module import failure.

All 13 tests uncollectable (reason: `ModuleNotFoundError: No module named
'AutoGrader.image_formats'`):

| # | Test | Would-be outcome if import worked |
|---|---|---|
| 1 | `test_psd_labelled_as_png_is_refused` | FAIL — PSD opens without `formats=` |
| 2 | `test_bmp_labelled_as_png_is_refused` | FAIL — BMP opens without `formats=` |
| 3 | `test_tiff_labelled_as_png_is_refused` | FAIL — TIFF opens without `formats=` |
| 4 | `test_jpeg_labelled_correctly_is_accepted` | GREEN — JPEG accepted at base too |
| 5 | `test_png_labelled_correctly_is_accepted` | GREEN — PNG accepted at base too |
| 6 | `test_gif_labelled_correctly_is_accepted` | GREEN — GIF accepted at base too |
| 7 | `test_webp_labelled_correctly_is_accepted` | GREEN — WEBP accepted at base too |
| 8 | `test_allowed_formats_match_image_formats` | FAIL — PILLOW_FORMATS dict absent from services.py |
| 9 | `test_pillow_formats_values_are_uppercase_strings` | FAIL — same |
| 10 | `test_validator_accepts_exactly_the_four_allowed_formats` | ERROR — inner import fails too |
| 11 | `test_validator_rejects_bmp` | ERROR — inner import fails too |
| 12 | `test_profile_image_rejects_bmp` | FAIL — no validator on serializer, BMP accepted |
| 13 | `test_profile_image_accepts_jpeg` | GREEN — JPEG accepted by bare ImageField |

Tests 4–7 and 13 are green at base because the base already accepts those four formats
(and JPEG is always accepted by Django's `ImageField`). They verify no false positives.

### Mutant battery (9 mutants)

| # | Mutant | Killing test | Notes |
|---|---|---|---|
| M1 | Drop `formats=_ALLOWED_PILLOW_NAMES` at `services.py:549` | `test_psd_labelled_as_png_is_refused` | PSD/BMP/TIFF decoders run, no exception |
| M2 | Remove `"image/webp": "WEBP"` from `PILLOW_FORMATS` | `test_allowed_formats_match_image_formats` | Keys no longer equal `IMAGE_FORMATS`; also `test_validator_accepts_exactly_the_four_allowed_formats` (WEBP rejected) |
| M3 | Drop `validators=[validate_image_format]` from `DirectAddStudentSerializer.profile_image` (`:658`) | `test_profile_image_rejects_bmp` | BMP accepted by bare ImageField |
| M4 | Drop `validators=[validate_image_format]` from `StudentRegistrationCompletionSerializer.profile_image` (`:834`) | **GAP** | No test exercises this serializer with an image upload (endpoint returns 410 GONE, H-152) |
| M5 | Drop `validators=[validate_image_format]` from `SchoolWithAdminSerializer.admin_profile_image` (`:1021`) | **GAP** | No test exercises this serializer's image field with a disallowed format |
| M6 | Drop `validators=[validate_image_format]` from `CustomUserSerializer` extra_kwargs (`:133`) | **GAP** | No test exercises this serializer with a BMP image |
| M7 | Drop `validate_image_format(img)` from `_ProfileImageForm.clean_profile_image` (`admin.py:28`) | **GAP** | No test exercises the admin form with a disallowed image format |
| M8 | Validator swallows exception: `return` instead of `raise ValidationError(...)` in `validate_image_format` | `test_validator_rejects_bmp` | Expects `ValidationError` |
| M9 | Remove `img.verify()` from `validate_image_format` | **GAP** | All test images are well-formed. `verify()` catches truncated/corrupt files within allowed formats (e.g. a JPEG with a valid SOI marker but truncated body). No test creates such a file. `formats=` already blocks disallowed format headers before `verify()` runs. |

**Gap summary:** M4, M5, M6, M7 — four write-path validators lack a dedicated killing
test. M9 — `verify()` removal not caught. Total: 5 gaps out of 9 mutants.

M4 is low-risk (endpoint is 410 GONE). M5–M7 are real gaps: a regression removing the
validator from one of those serializers or the admin form would go undetected by the
H-267 test suite alone. The near suites' existing serializer tests may partially cover
M5–M6 but are not written to assert format validation. M9 is an inherent limitation:
testing `verify()` requires crafting a corrupt-but-openable image, which is brittle across
Pillow versions.

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
4. Red run: 13 H-267 tests against base code → Errors 1 (import failure), Ran 0.
5. Green run: 13 H-267 tests at the tip → Ran 13, OK.
6. Mutant plants A1–A4 (M1, M2, M3, M8 — the four killed mutants).
7. Near suites: assignments, classrooms, users.
8. Full suite in private venv under `systemd-run --scope -p MemoryMax=8G`.
