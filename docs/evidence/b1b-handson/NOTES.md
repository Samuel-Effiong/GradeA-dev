# B1b hands-on test (brief 7, SM -98, owner go 2026-11-11): notes

Base origin/staging a0478508e, branch task/b1b-handson (evidence only),
worktree Grade-Automator-Plus-b1b-handson.
- venv: ~/Documents/Virtualenvs/b1b_handson_env (new, python3.12, from this
  tree's requirements.txt + requirements-dev.txt). Deleted at the end.
- DB: gap_handson, fresh. BLOCKED: waiting on the SM's ruling on which DB
  role may create it (the only local credential is the app's "postgres" URL).
- Unblocked: the OWNER allowed gap_handson through the app's local URL, via manage.py only. Created through Django's _nodb_cursor; a private redis on :6391; the settings_handson override (not committed).
- Run 1 on a0478508e (DB1, dropped). Run 2 on ef1080075 (DB2, kept for the BUG-2/BUG-3 reruns). Results, bugs, wording, Cloudinary key counts and AI use are in REPORT.md. Raw logs are not committed (owner rule).
- Run 3 on b3c900aaf (DB2): BUG-3 rerun (a) cancel unstarted upload PASS, (b) normal upload charge guard PASS (12,146 consumed = wallet delta). BUG-2 rerun still pending.
