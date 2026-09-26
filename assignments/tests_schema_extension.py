"""assignments/schema.py - the drf-spectacular polymorphic extension.

This file had 0.0% coverage, and chasing that turned up a defect rather
than just a missing test: THE EXTENSION HAS NEVER BEEN ACTIVE.

drf-spectacular registers an OpenApiSerializerExtension when the module
defining it is imported - subclassing is the registration. Nothing imports
`assignments.schema`. The only reference to it anywhere is:

    SPECTACULAR_SETTINGS = {
        ...
        "EXTENSIONS": {
            "polymorphic_assignment": "assignments.schema.PolymorphicAssignmentExtension",
        },
    }

but `EXTENSIONS` is not a drf-spectacular setting. Verified against
drf-spectacular 0.28.0's own DEFAULTS: the real keys are EXTENSIONS_INFO,
EXTENSIONS_ROOT and EXTERNAL_DOCS (all for OpenAPI `x-` vendor extensions,
nothing to do with serializer extensions). Unknown keys are ignored
silently, so the module is never imported, the class never registers, and
the generated document has no discriminator at all.

Consequence: `/assignments/` returns a different shape to a teacher than
to a student, and the published schema does not say so. A generated client
gets the teacher model only and has no way to tell the two apart.

The tests below therefore do two jobs:
  * unit-test map_serializer's output, so the mapping is correct and stays
    correct for whenever it IS wired up; and
  * pin the current inactive state, so that wiring it up FAILS this file
    and forces a deliberate decision - because turning it on changes the
    published OpenAPI contract that the frontend and mobile clients
    generate from, which is not a change to make silently.
"""

from django.test import TestCase

from assignments.schema import PolymorphicAssignmentExtension


class PolymorphicAssignmentExtensionUnitTest(TestCase):
    """The mapping itself, independent of whether it is registered."""

    def setUp(self):
        self.schema = PolymorphicAssignmentExtension(None).map_serializer(
            None, "response"
        )

    def test_it_targets_the_assignment_list_serializer(self):
        self.assertEqual(
            PolymorphicAssignmentExtension.target_class,
            "assignments.serializers.AssignmentListSerializer",
        )

    def test_the_mapping_declares_both_teacher_and_student_shapes(self):
        """
        The whole point: one endpoint returns two shapes depending on who
        asks. If the mapping lost an entry, a generated client would parse
        one role's response with the other role's model.
        """
        self.assertEqual(self.schema["type"], "object")
        self.assertEqual(self.schema["discriminator"]["propertyName"], "user_type")

        mapping = self.schema["discriminator"]["mapping"]
        self.assertEqual(
            mapping["teacher"], "#/components/schemas/AssignmentListSerializer"
        )
        self.assertEqual(
            mapping["student"], "#/components/schemas/AssignmentListStudentSerializer"
        )

    def test_the_discriminator_property_is_declared_and_required(self):
        """
        A discriminator absent from `properties`/`required` is invalid
        OpenAPI: some generators reject the document, others emit a client
        that never sets the field.
        """
        self.assertIn("user_type", self.schema["properties"])
        self.assertEqual(
            self.schema["properties"]["user_type"]["enum"], ["teacher", "student"]
        )
        self.assertIn("user_type", self.schema["required"])

    def test_the_target_serializer_still_exists(self):
        """
        target_class is a hand-written import path. A renamed serializer
        would leave the extension pointing at nothing - and because it is
        resolved lazily, nothing would say so.
        """
        import assignments.serializers as serializers_module

        attribute = PolymorphicAssignmentExtension.target_class.rsplit(".", 1)[-1]
        self.assertTrue(hasattr(serializers_module, attribute))

    def test_both_mapped_serializers_still_exist(self):
        import assignments.serializers as serializers_module

        for ref in self.schema["discriminator"]["mapping"].values():
            component = ref.rsplit("/", 1)[-1]
            self.assertTrue(
                hasattr(serializers_module, component),
                f"schema.py maps to {component!r}, which no longer exists in "
                "assignments.serializers",
            )


class PolymorphicExtensionIsNotWiredUpTest(TestCase):
    """
    Characterises the defect described in this module's docstring.

    These assertions describe what the project ships TODAY, not what it
    should ship. Wiring the extension up (importing assignments.schema at
    startup - e.g. from AssignmentsConfig.ready()) will break every test in
    this class, which is the intent: that change alters the published
    OpenAPI document and should be made deliberately, with the frontend and
    mobile clients regenerated, not as a drive-by.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from drf_spectacular.generators import SchemaGenerator

        cls.components = SchemaGenerator().get_schema(request=None, public=True)[
            "components"
        ]["schemas"]

    def test_extensions_is_not_a_real_drf_spectacular_setting(self):
        """
        The root cause, asserted directly so an upgrade that ADDS an
        `EXTENSIONS` setting is noticed rather than assumed.
        """
        import re
        from pathlib import Path

        import drf_spectacular.settings as spectacular_settings

        source = Path(spectacular_settings.__file__).read_text()
        default_keys = set(re.findall(r"^\s{4}'([A-Z_]+)':", source, re.M))

        self.assertNotIn(
            "EXTENSIONS",
            default_keys,
            "drf-spectacular now has an EXTENSIONS setting - the "
            "SPECTACULAR_SETTINGS entry in AutoGrader/settings.py may finally "
            "be doing something, so re-check this whole module",
        )

    def test_the_settings_entry_that_was_meant_to_register_it_is_inert(self):
        from django.conf import settings

        self.assertEqual(
            settings.SPECTACULAR_SETTINGS["EXTENSIONS"]["polymorphic_assignment"],
            "assignments.schema.PolymorphicAssignmentExtension",
            "the inert settings entry changed - re-check whether the "
            "extension is now registered some other way",
        )

    def test_no_production_module_imports_the_extension(self):
        """
        The root cause, checked statically.

        Registration IS import for drf-spectacular, so "is the
        discriminator in the generated schema?" is the wrong question to
        ask from a test - THIS FILE imports assignments.schema, which
        registers the extension for the rest of the process. Running the
        suite therefore produces a schema that production never serves.
        That is not a flaw in the test; it is the clearest possible
        demonstration of the bug: one stray import is the entire
        difference between the contract being published and not.

        So this asserts what actually determines production behaviour -
        that no shipped module imports it - which no test can contaminate.
        """
        import pathlib

        root = pathlib.Path(__file__).resolve().parent.parent
        importers = []
        for path in root.rglob("*.py"):
            relative = path.relative_to(root).as_posix()
            if (
                relative.startswith(("assignments/tests", "venv/", ".venv/"))
                or "/migrations/" in relative
                or path.name == "schema.py"
            ):
                continue
            text = path.read_text(errors="ignore")
            if "assignments.schema" in text or "from .schema import" in text:
                # The inert SPECTACULAR_SETTINGS string is not an import.
                if (
                    "SPECTACULAR_SETTINGS" in text
                    and "import"
                    not in text.split("assignments.schema")[0].rsplit("\n", 1)[-1]
                ):
                    continue
                importers.append(relative)

        self.assertEqual(
            importers,
            [],
            "something now imports assignments.schema, so the polymorphic "
            "extension registers in production and the published OpenAPI "
            "contract has changed - see this module's docstring, and note "
            "the mapping's refs must be corrected in the same change",
        )

    def test_the_components_the_mapping_points_at_do_not_exist(self):
        """
        Even if the extension were registered as-is, its refs would dangle:
        drf-spectacular strips the "Serializer" suffix, so the real
        components are `AssignmentList` and `StudentAssignmentList`, not
        `AssignmentListSerializer`/`AssignmentListStudentSerializer`.

        So wiring it up is NOT a one-line import - the mapping has to be
        corrected in the same change or the discriminator resolves to
        nothing.
        """
        for missing in (
            "AssignmentListSerializer",
            "AssignmentListStudentSerializer",
        ):
            self.assertNotIn(missing, self.components)

        # The names drf-spectacular actually generates, recorded so the
        # corrected mapping is obvious when someone does fix this.
        self.assertIn("AssignmentList", self.components)
        self.assertIn("StudentAssignmentList", self.components)
