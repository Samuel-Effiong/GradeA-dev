"""
Swagger docs for the user list's filters and sort (brief 6, the owner
approved it as a beta bug fix on 2026-11-11; SM -cf).

On GET /api/v1/users every UserEnrollmentFilter filter showed only its
name, and the `ordering` text was too hard for the owner to follow. The fix
changes docs only: each filter gets a plain description, and `ordering` is
rewritten in everyday words (owner, 2026-11-11: "highest first", not
"descending"; "empty", not "null"; with an example).
"""

from drf_spectacular import extensions
from rest_framework.test import APITestCase

from users.filters import UserEnrollmentFilter
from users.user_list import ordering_values

#: Words the owner asked us not to use in the ordering text.
JARGON = ("ascending", "descending", "null", "raw", "tiebreak", "tie-break")


def list_parameters(test):
    from drf_spectacular.generators import SchemaGenerator

    # The same snapshot/restore as users.tests_user_list_sorting: schema
    # generation changes every extension's target_class.
    registered = {
        ext
        for base in (
            extensions.OpenApiAuthenticationExtension,
            extensions.OpenApiSerializerExtension,
            extensions.OpenApiSerializerFieldExtension,
            extensions.OpenApiViewExtension,
            extensions.OpenApiFilterExtension,
        )
        for ext in base._registry
    }
    saved = {ext: ext.__dict__.get("target_class") for ext in registered}

    def restore():
        for ext, target in saved.items():
            ext.target_class = target

    test.addCleanup(restore)
    schema = SchemaGenerator().get_schema(request=None, public=True)
    operation = schema["paths"]["/api/v1/users"]["get"]
    return {p["name"]: p for p in operation.get("parameters", [])}


def own_text(description):
    """The description without spectacular's generated list of choices
    ("* `X` - Y" lines)."""
    text = description or ""
    if text.startswith("* `"):
        return ""
    return text.partition("\n\n*")[0].strip()


class FilterDocsTests(APITestCase):
    def test_every_filter_has_its_own_description(self):
        params = list_parameters(self)
        names = sorted(UserEnrollmentFilter.base_filters)
        self.assertGreaterEqual(len(names), 19)  # else the loop proves little

        for name in names:
            with self.subTest(name):
                self.assertIn(name, params)
                text = own_text(params[name].get("description"))
                self.assertNotEqual(text, "")
                self.assertNotEqual(text.lower(), name.lower())
                # A sentence, not a label.
                self.assertGreaterEqual(len(text.split()), 6, text)

    def test_the_signal_filters_say_teachers_only_and_super_admin_only(self):
        params = list_parameters(self)
        for name in (
            "signals_min",
            "at_80_percent",
            "frequent_user",
            "active_last_week",
            "is_power_grader",
        ):
            with self.subTest(name):
                text = own_text(params[name].get("description")).lower()
                self.assertIn("teacher", text)
                self.assertIn("super admin", text)

    def test_date_filters_say_the_day_itself_is_included(self):
        params = list_parameters(self)
        for name in (
            "last_login_after",
            "last_login_before",
            "activated_after",
            "activated_before",
        ):
            with self.subTest(name):
                self.assertIn(
                    "included", own_text(params[name].get("description")).lower()
                )


class OrderingDocsTests(APITestCase):
    def ordering_text(self):
        return list_parameters(self)["ordering"].get("description", "")

    def test_every_key_has_its_own_line(self):
        text = self.ordering_text()
        keys = [v for v in ordering_values() if not v.startswith("-")]
        lines = text.splitlines()

        for key in keys:
            with self.subTest(key):
                self.assertTrue(
                    any(line.startswith(f"- `{key}`") for line in lines),
                    f"no line for {key}",
                )

    def test_it_says_how_to_sort_highest_first_and_gives_an_example(self):
        text = self.ordering_text()

        self.assertIn("highest first", text)
        self.assertIn("`-signals,email`", text)
        self.assertIn("empty", text)

    def test_it_spells_out_the_plan_rank_and_the_credits(self):
        text = " ".join(self.ordering_text().split())  # lines are wrapped

        self.assertIn("Trial, Beta, Standard, Pro, Power, Custom", text)
        self.assertIn("1,000", text)  # credits shown vs stored (CONVERSION_FACTOR)

    def test_it_uses_none_of_the_jargon(self):
        text = self.ordering_text().lower()
        for word in JARGON:
            with self.subTest(word):
                self.assertNotIn(word, text)
