# H-273 venv rebuild

Date: 2026-10-30

## Environment change report (SM point (a))

The h273 venv was rebuilt from scratch. The previous venv was created from
`requirements.txt` only. `freezegun` was then installed ad-hoc (unreported
environment change — acknowledged).

## Rebuild

```
rm -rf ~/Documents/Virtualenvs/AutoGrader_env_h273
python3.12 -m venv ~/Documents/Virtualenvs/AutoGrader_env_h273
source ~/Documents/Virtualenvs/AutoGrader_env_h273/bin/activate
pip install --upgrade pip
pip install -r requirements.txt -r requirements-dev.txt
```

This matches the batch-31 gate pattern: "the bundle's requirements.txt and
requirements-dev.txt are byte-identical to H-271's, from which it was built"
(beta-batch-31/NOTES.md).

## Freeze

226 packages. Archive: `~/Documents/Projects/GAP-a0-runs/h273/h273_venv_freeze_v2.txt`

SHA-256: `52da179f36d7e0c59f01407641a3221fc8d16ee6b93170c6127ac26388cc147e`

Key packages confirmed present:
- pytest==9.1.1 (requirements-dev.txt)
- freezegun==1.5.5 (requirements-dev.txt, transitive)
- Django==5.2.17
- djangorestframework==3.18.2

Old venv (requirements.txt only) is superseded and deleted.
