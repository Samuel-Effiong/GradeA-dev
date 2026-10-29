# flake8: noqa
"""Survey every ModelSerializer subclass for DRF 3.18 auto-generated
UniqueConstraint validators.

    python -I survey_unique_constraint_validators.py

Run from the worktree root with DJANGO_SETTINGS_MODULE=AutoGrader.settings.
"""

import importlib
import os
import sys

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "AutoGrader.settings")

sys.path.insert(0, os.getcwd())

import django

django.setup()

from rest_framework.serializers import ModelSerializer
from rest_framework.validators import UniqueTogetherValidator

SERIALIZER_MODULES = [
    "ai_processor.serializers",
    "assignments.serializers",
    "audit.serializers",
    "billing.serializers",
    "classrooms.serializers",
    "dashboard.serializers",
    "students.serializers",
    "users.serializers",
]

flagged = []
checked = 0

for mod_name in SERIALIZER_MODULES:
    mod = importlib.import_module(mod_name)
    for attr_name in sorted(dir(mod)):
        obj = getattr(mod, attr_name)
        if not isinstance(obj, type):
            continue
        if not issubclass(obj, ModelSerializer):
            continue
        if obj is ModelSerializer:
            continue
        if obj.__module__ != mod_name:
            continue

        checked += 1
        model = getattr(getattr(obj, "Meta", None), "model", None)
        model_name = f"{model.__module__}.{model.__name__}" if model else "no model"

        try:
            validators = obj().get_validators()
        except Exception as exc:
            print(f"  SKIP {mod_name}.{attr_name} ({model_name}): {exc}")
            continue

        uc_validators = []
        for v in validators:
            if isinstance(v, UniqueTogetherValidator):
                uc_validators.append(v)

        if uc_validators:
            for v in uc_validators:
                fields = list(v.fields) if hasattr(v, "fields") else "?"
                print(
                    f"  FLAGGED {mod_name}.{attr_name} "
                    f"(model={model_name}): "
                    f"UniqueTogetherValidator(fields={fields})"
                )
                meta_validators = getattr(
                    getattr(obj, "Meta", None), "validators", None
                )
                if meta_validators is not None and len(meta_validators) == 0:
                    print(f"    -> Meta.validators = [] (already emptied)")
                flagged.append(
                    (mod_name, attr_name, model_name, fields, meta_validators)
                )
        else:
            print(
                f"  ok {mod_name}.{attr_name} ({model_name}): {len(validators)} validators, none from UniqueConstraint"
            )

print()
print(
    f"Checked {checked} ModelSerializer subclasses across {len(SERIALIZER_MODULES)} modules."
)
print(f"Flagged: {len(flagged)}")
for mod_name, attr_name, model_name, fields, meta_v in flagged:
    emptied = (
        " (emptied by validators=[])"
        if meta_v is not None and len(meta_v) == 0
        else " (NOT emptied)"
    )
    print(f"  {mod_name}.{attr_name} model={model_name} fields={fields}{emptied}")
