"""The metadata allow-list: what may enter `metadata`, `before` and `after`.

FR-A-04 / NFR-CMP-02: no student name, email or answer text in any log. The
data model says this is "enforced by the emitter's allow-list, not by
convention", so these tests feed it the things a careless call site would
send and check that none of them survive.
"""

from django.test import SimpleTestCase

from .metadata import (
    ALLOWED_KEYS,
    MAX_BYTES,
    MAX_ITEM_STRING,
    MAX_KEYS,
    MAX_LIST_ITEMS,
    MAX_STRING,
    sanitise,
)

# Words that mean "this key carries a person's data or their work". No key
# containing one may ever be added to the allow-list.
FORBIDDEN_FRAGMENTS = (
    "name",
    "email",
    "answer",
    "text",
    "content",
    "body",
    "title",
    "phone",
    "address",
    "comment",
    "feedback",
    "note",
    "password",
    "token",
)


class AllowListTest(SimpleTestCase):
    def test_the_allow_list_is_not_empty_and_holds_only_lowercase_snake_case(self):
        self.assertGreater(len(ALLOWED_KEYS), 10)
        for key in ALLOWED_KEYS:
            self.assertRegex(key, r"^[a-z][a-z0-9_]{0,39}$")

    def test_no_allowed_key_names_a_person_or_their_work(self):
        for key in ALLOWED_KEYS:
            for fragment in FORBIDDEN_FRAGMENTS:
                with self.subTest(key=key, fragment=fragment):
                    self.assertNotIn(fragment, key)


class SanitiseTest(SimpleTestCase):
    def sample_key(self):
        return sorted(ALLOWED_KEYS)[0]

    def test_allowed_scalars_pass_through_unchanged(self):
        keys = sorted(ALLOWED_KEYS)[:5]
        given = dict(zip(keys, [3, "ok", True, None, 1.5], strict=True))
        clean, problems = sanitise(given)
        self.assertEqual(clean, given)
        self.assertEqual(problems, [])

    def test_none_and_empty_mean_no_metadata_and_no_problem(self):
        self.assertEqual(sanitise(None), ({}, []))
        self.assertEqual(sanitise({}), ({}, []))

    def test_an_unknown_key_is_dropped_and_named_in_the_problems(self):
        clean, problems = sanitise({self.sample_key(): 1, "verbosity": 9})
        self.assertEqual(clean, {self.sample_key(): 1})
        self.assertEqual(problems, [("verbosity", "not on the allow-list")])

    def test_a_key_that_looks_like_personal_data_is_dropped_before_it_is_reported(self):
        """A careless call site can pass a name as the KEY. It is never echoed
        into the problem list, which ends up in an operational log."""
        clean, problems = sanitise({"Jane O'Brien": 1, "kid@example.com": 2})
        self.assertEqual(clean, {})
        self.assertEqual(
            problems,
            [("<invalid-key>", "not on the allow-list")] * 2,
        )

    def test_a_non_string_key_is_dropped(self):
        clean, problems = sanitise({42: 1})
        self.assertEqual(clean, {})
        self.assertEqual(len(problems), 1)

    def test_a_nested_object_is_dropped(self):
        key = self.sample_key()
        clean, problems = sanitise({key: {"inner": 1}})
        self.assertEqual(clean, {})
        self.assertEqual(problems, [(key, "value is not a scalar or short list")])

    def test_a_list_of_scalars_up_to_the_limit_is_kept(self):
        key = self.sample_key()
        given = {key: list(range(MAX_LIST_ITEMS))}
        self.assertEqual(sanitise(given), (given, []))

    def test_a_longer_list_is_dropped(self):
        key = self.sample_key()
        clean, problems = sanitise({key: list(range(MAX_LIST_ITEMS + 1))})
        self.assertEqual(clean, {})
        self.assertEqual(problems, [(key, "list too long")])

    def test_a_list_holding_an_object_is_dropped(self):
        key = self.sample_key()
        clean, _ = sanitise({key: [{"a": 1}]})
        self.assertEqual(clean, {})

    def test_a_string_over_the_limit_is_dropped_so_answer_text_cannot_ride_along(self):
        key = self.sample_key()
        clean, problems = sanitise({key: "x" * (MAX_STRING + 1)})
        self.assertEqual(clean, {})
        self.assertEqual(problems, [(key, "string too long")])
        self.assertEqual(sanitise({key: "x" * MAX_STRING})[0], {key: "x" * MAX_STRING})

    def test_a_string_with_a_nul_byte_is_dropped(self):
        key = self.sample_key()
        self.assertEqual(
            sanitise({key: "a\x00b"}), ({}, [(key, "string contains a NUL byte")])
        )

    def test_a_list_item_over_its_limit_is_dropped(self):
        key = self.sample_key()
        clean, _ = sanitise({key: ["x" * (MAX_ITEM_STRING + 1)]})
        self.assertEqual(clean, {})

    def test_a_value_shaped_like_an_email_address_is_dropped_wherever_it_hides(self):
        key = self.sample_key()
        for value in ("kid@example.com", "contact: kid@example.com now"):
            with self.subTest(value=value):
                clean, problems = sanitise({key: value})
                self.assertEqual(clean, {})
                self.assertEqual(problems, [(key, "looks like an email address")])
        self.assertEqual(sanitise({key: ["fine", "kid@example.com"]})[0], {})

    def test_non_finite_numbers_are_dropped(self):
        key = self.sample_key()
        for value in (float("nan"), float("inf")):
            with self.subTest(value=value):
                self.assertEqual(sanitise({key: value})[0], {})

    def test_more_than_the_maximum_number_of_keys_is_dropped(self):
        keys = sorted(ALLOWED_KEYS)
        if len(keys) <= MAX_KEYS:
            self.skipTest("allow-list smaller than the key limit")
        clean, problems = sanitise({k: 1 for k in keys[: MAX_KEYS + 1]})
        self.assertEqual(clean, {})
        self.assertEqual(problems, [("*", "too many keys")])

    def test_a_value_that_is_not_an_object_is_dropped_whole(self):
        for value in ([("a", 1)], "text", 5):
            with self.subTest(value=value):
                clean, problems = sanitise(value)
                self.assertEqual(clean, {})
                self.assertEqual(problems, [("*", "not an object")])

    def test_the_serialised_size_is_bounded(self):
        keys = sorted(ALLOWED_KEYS)[:MAX_KEYS]
        big = {k: ["y" * MAX_ITEM_STRING] * MAX_LIST_ITEMS for k in keys}
        clean, problems = sanitise(big)
        self.assertEqual(clean, {})
        self.assertEqual(problems, [("*", "too large")])
        self.assertGreater(MAX_BYTES, 0)

    def test_the_input_is_never_modified(self):
        given = {self.sample_key(): 1, "verbosity": 2}
        copy = dict(given)
        sanitise(given)
        self.assertEqual(given, copy)
