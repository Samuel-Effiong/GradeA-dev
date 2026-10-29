# H-271: static files are collected and served in the deployed image (design, written before any code)

Owner 2026-10-29: in batch 31; scope "collectstatic + WhiteNoise".

## The bug

On staging (667dc62e, 2026-10-29) `/admin/login/` answers 500 and `/static/admin/css/base.css` 404.
- The Dockerfile has a `# Collect static files.` comment with no command under it, on every version
  back to `main`. Nothing ever runs `collectstatic`.
- Production storage is `ManifestStaticFilesStorage` (settings.py, `ENVIRONMENT == "prod"`). Its
  `{% static %}` lookup raises `ValueError: Missing staticfiles manifest entry` when no manifest
  exists, so every page that links CSS/JS (the Django admin, DRF's browsable pages) is a 500.
- Even with a manifest, nothing serves `/static/`: `DEBUG` is False outside `local`, gunicorn serves
  no files, and there is no static-file middleware. The admin would render unstyled.

## The change

1. **`whitenoise==6.12.0`** in requirements.txt (released 2026-02-27; Django 5.2 and Python 3.12
   supported). Production dependency: it serves requests.
2. **Middleware**: `whitenoise.middleware.WhiteNoiseMiddleware` directly after
   `django.middleware.security.SecurityMiddleware`, in every environment (WhiteNoise's documented
   position; it must come before anything that might answer the request first).
3. **Production storage**: `whitenoise.storage.CompressedManifestStaticFilesStorage` replaces
   `ManifestStaticFilesStorage`. Same hashed-name manifest (so the cache-busting the settings comment
   asks for is kept), plus gzip copies WhiteNoise serves to clients that accept them. Non-prod
   storage is unchanged.
4. **`STATIC_ROOT` becomes `BASE_DIR/staticfiles`** (was `BASE_DIR/static`). The repo tracks 103
   files in `static/` (a TinyMCE demo `editor.html`, `agriculture.pdf`, the TinyMCE bundle), last
   touched in early WIP commits and referenced by no code. With `static/` as `STATIC_ROOT`,
   WhiteNoise would serve them publicly. A separate output folder keeps them unserved; deleting them
   is a separate clean-up, not this row. `staticfiles/` is added to .gitignore.
5. **Dockerfile**: `collectstatic --noinput` runs at container start, before gunicorn, in the
   existing `CMD` (`set -xe` stops the start if it fails). Not at build time: settings.py requires
   SECRET_KEY, Stripe, Cloudinary and Redis variables at import, and passing secrets into the image
   build is worse than a few seconds at start. `/app` is already owned by `appuser`, so the write
   works. The empty `# Collect static files.` comment is replaced by one that says where it runs.

No migration. No API change. Media storage (Cloudinary) is not touched.

## Gate (expectations get written in NOTES and committed before any run)

Tests `AutoGrader/tests_h271_static_files.py`:
- Dockerfile: the final `CMD` runs `python manage.py collectstatic --noinput` before `gunicorn`.
- Production settings, loaded in a fresh process with `ENVIRONMENT=prod` and fake values (the
  `tests_school_admin_frontend_domain` pattern): staticfiles backend is
  `CompressedManifestStaticFilesStorage`; WhiteNoise is directly after SecurityMiddleware;
  `STATIC_ROOT` ends in `staticfiles`.
- End to end with DEBUG=False, the production storage and a temp `STATIC_ROOT`: after
  `collectstatic`, `/admin/login/` answers 200 and the hashed `admin/css/base.css` it links answers
  200 `text/css`; a file from the tracked `static/` folder (`editor.html`) answers 404.
- Guard (passes before and after): without `collectstatic`, rendering `/admin/login/` under the
  manifest storage raises `Missing staticfiles manifest entry`, which is the staging 500.

Red: call-site revert of Dockerfile and settings.py to the base. The requirement stays installed
(the venv has it either way). Near suite, seam and mutants as usual. The deploy proof follows batch
31's staging refresh: `/admin/login/` 200 and its CSS 200 on staging.
