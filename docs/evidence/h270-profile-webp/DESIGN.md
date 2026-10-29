# H-270: profile pictures are saved as WebP (design, written before any code)

Owner request 2026-10-27; owner rulings (fixed, not reopened here):
- profile pictures only (`CustomUser.profile_image`). Answer and assignment images stay JPEG for the
  AI; `compress_image_for_upload` and `AssignmentProcessingService` are not touched;
- the accept list stays JPEG/PNG/GIF/WEBP (H-267's `validate_image_format`); BMP/TIFF are still refused;
- pictures already stored are left as they are: no data migration, no rewrite job.
Owner 2026-10-29: batch 31 waits for this row.

Base: `task/h267-pillow-formats` evidence tip 94a52815 (H-267 is not on beta yet; this row needs its
validator and admin form).

## 1. Where: one conversion point

`CustomUser.save()` (users/models.py) converts before `super().save()` when `profile_image` holds a
file that has not been stored yet (`FieldFile._committed is False`). That is the state Django gives
a field right after an upload is assigned, and every write path reaches it:

| path | how the file reaches the model |
|---|---|
| `CustomUserSerializer` (users/serializers.py) create/update | `ModelSerializer` -> `instance.save()` |
| `DirectAddStudentSerializer` (classrooms/serializers.py:655) | `profile_image` kwarg -> user create -> `save()` |
| `StudentRegistrationCompletionSerializer` (classrooms/serializers.py:831) | assigned -> `save()` |
| `SchoolWithAdminSerializer.admin_profile_image` (classrooms/serializers.py:1018) | kwarg -> user create -> `save()` |
| admin `_ProfileImageForm` (users/admin.py) | `construct_instance` -> `save()` |

Why the model and not the 5 serializers/forms: one place, and a future write path converts without
anyone remembering. A save that does not change the picture finds `_committed is True` (the file is
already in storage) and does nothing: no download, no re-encode. `QuerySet.update()` and
`bulk_create` never take files in this codebase (checked: no `profile_image=` outside the 5 paths
above), so they need nothing.

The conversion code lives in `AutoGrader/image_formats.py` next to H-267's validator, as
`to_profile_webp(file) -> ContentFile`. The model calls it.

## 2. Encoding

In this order:
1. `Image.open(..., formats=_ALLOWED_PILLOW_NAMES)`: the same four decoders H-267 allows. The
   validator already ran on every path; this repeats the guard at the point that decodes.
2. Animated input (GIF, animated WebP): **first frame only**. A profile picture is a still. An
   animated WebP keeps every frame and costs size for nothing.
3. `ImageOps.exif_transpose`: phone photos stored sideways with an orientation tag are turned the
   right way up before the tag is dropped.
4. Colour: if the image carries an ICC profile, convert it to sRGB (`ImageCms`, bundled with
   Pillow 13), then drop the profile. A Display-P3 phone photo keeps its colours, and the stored file
   carries no profile.
5. Mode: images with transparency (RGBA, LA, P with a transparency key, PA) -> RGBA; everything
   else, including CMYK JPEG and 16-bit PNG -> RGB.
6. Size: longest side at most **1024 px** (`thumbnail`, LANCZOS; never enlarged). Profile pictures
   show at avatar size; a 12 MP phone photo shrinks from about 3 MB to tens of KB.
7. Save as **lossy WebP, quality 80, method 6**. Lossy WebP keeps the alpha channel, so transparent
   PNG/GIF pictures stay transparent.

**Metadata: none.** The pixels are copied into a fresh `Image` before saving, so nothing in
`img.info` (EXIF with GPS, XMP, ICC, comments) can reach the encoder, whatever Pillow's WebP plugin
passes through by default. The test checks the stored bytes: no `EXIF`, `XMP ` or `ICCP` RIFF chunk.

An upload that is already WebP goes through the same steps. A WebP can carry EXIF/XMP with GPS too,
so it is not skipped.

If decoding fails here, the error is raised, not swallowed. Every path validated the file first, so
this means a bug. Storing the original instead would keep the GPS data the row exists to remove.

## 3. Names and storage

- The converted file is named `<original stem>.webp`. `upload_to=get_user_name` is unchanged, and it
  takes the extension from that name, so the stored path ends `.webp`
  (`profile_pics/<id>/<email name>_<date>_.webp`). No migration (`upload_to` and the field are unchanged).
- Storage: Cloudinary (`MediaCloudinaryStorage`) in prod/dev, `FileSystemStorage` in tests. Both store
  `.webp` as an image. Tests use `FileSystemStorage` in a temp `MEDIA_ROOT` (`.env` says
  `ENVIRONMENT=local`, which would otherwise mean real Cloudinary).
- users/signals.py: no receiver deletes or reads profile files. `profile_image` is only compared as a
  string to decide cache bumps. A new picture changes the stored name, so the bump still happens.
  When a picture is replaced, the old file stays in storage, as it does today. Deleting it is outside
  this row: it would be a new storage behaviour, and existing pictures are to be left alone.
- `profile_image_url` (the Google avatar URL kept at Google sign-in) is a URL, not a stored file, so
  it is not touched.
- Known oddity, unchanged: on create, `get_user_name` runs before the row has an id, so the folder is
  `profile_pics/None/`. Not this row's job; noted for the backlog.

## 4. Client and API

The response shape does not change. `profile_image` is still a URL, which now ends `.webp` for
pictures uploaded after this lands. Old pictures keep their `.jpg`/`.png` URLs. Every browser the
frontend supports shows WebP in `<img>`. Nothing on the client needs to change.

## 5. Gate (expectations get written in NOTES and committed before any run)

Tests (`users/tests_h270_profile_webp.py`), red as written against the call-site revert (the
`save()` hook removed):
- each of the 5 paths stores a `.webp` file whose bytes open as `WEBP`;
- a JPEG with EXIF GPS + orientation in, out: no EXIF/XMP/ICCP chunk, and the picture is the right
  way up (the tagged-sideways input comes out with the expected width > height);
- a transparent PNG stays transparent (alpha channel present, a known pixel transparent);
- an animated GIF stores a single-frame WebP;
- a 3000 px picture is stored at a 1024 px longest side; a 200 px picture is not enlarged;
- a WebP upload carrying EXIF comes out without it;
- a save that does not change the picture does not re-encode (the stored name and bytes are
  unchanged after `user.save()` and after an update of `first_name`);
- an old stored JPEG picture still loads and serializes unchanged (no conversion on read);
- a Display-P3 tagged image comes out with no ICC profile.

Gate pack: NOTES expectations committed first, red run, makemigrations (no changes), seam check,
near suites `users classrooms assignments.tests_h267_pillow_formats` with Ran N from the
DiscoverRunner id list, and mutants with declared fragments:
- M1: the hook is skipped;
- M2: metadata copied through (`exif=img.info.get("exif")`);
- M3: no `exif_transpose`;
- M4: no resize;
- M5: alpha flattened to RGB;
- M6: the `_committed` check removed (re-encode on every save);
- M7: the extension is left as the original.

Run in the private venv `h266_list_errors_dict_env` (batch-30 pins, Pillow 13.0.0).

Any H-267 test whose stored-file assertions assume the original format will be named in NOTES
before the run, not discovered during it.
