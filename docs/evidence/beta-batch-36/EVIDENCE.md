# Beta Batch 36

Owner go 2026-11-05: "every fix that we have worked on and haven't pushed to
beta, commit them and push them".

Base: beta a3ca097d8 (= origin = backup). Merged with --no-ff, no conflicts:
- H-288 teacher summary scoped by session owner (task/h288-teacher-summary-scope b6882171f)
- H-291 emailed links encode the address (task/h291-email-link-encoding 8ef4ac333)
- H-292 NOTHING_TO_GRADE coded refusal + typo (task/h292-nothing-to-grade 0da0b46ce)
- H-293 audit events filter by error_class (task/h293-audit-error-class-filter 50f65bf7c)
- H-294 backend message audit, docs only (task/h294-backend-message-audit 9455708fa)
- H-295 course without session 400 (was 500); status/message refusal text (inside H-296's branch)
- H-296 owner-approved message wording (task/h296-message-rewording 8e1343eaa)

Each task's own red/green/plant/near evidence is in its docs/evidence folder.

Merged-tree near run (users classrooms assignments students billing
AutoGrader audit dashboard): Ran 7519, OK (skipped=24). Full log:
GAP-a0-runs/b36-near.log. No full suite was run (owner's say-so not given
for one); CI runs the full Tests job on push and Railway waits for CI.
Co-author trailers: 0.
