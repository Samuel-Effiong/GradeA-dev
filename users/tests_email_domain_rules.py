"""
Personal vs business email rules.

The product keeps a person's individual account and their school account
deliberately separate, and the email domain is what tells them apart:

  * individual (TEACHER) accounts must use a PERSONAL mailbox
  * school admin accounts, and teachers enrolled under a school's license,
    must use a BUSINESS mailbox

That rule was previously implemented as a single blocklist -- "business"
meant "domain not among 22 named consumer providers" -- with three
consequences this suite pins shut:

1. The business gate was one character wide. gmail.com was blocked but
   googlemail.com, yahoo.co.uk, proton.me, gmx.net and every throwaway-mail
   provider all classified as *business*, so any of them could mint a school
   admin or be enrolled under a license.
2. The check only ran on account creation or on an email change, so PATCHing
   an existing teacher on a personal address to SCHOOL_ADMIN produced exactly
   the state the rule forbids.
3. The domain split was `email.split("@")[-1].lower()`, which returns the
   whole string when there is no "@", raises AttributeError on None, and
   leaves a trailing space on an unstripped value.

Both helpers now fail CLOSED: an address that is malformed, or that comes
from a disposable provider, is neither personal nor business and is refused
on both tracks.
"""

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings

from users.models import UserTypes
from users.serializers import CustomUserSerializer
from users.utils import (
    email_domain,
    is_business_email,
    is_disposable_email,
    is_exempt_email_domain,
    is_personal_email,
)

User = get_user_model()

PASSWORD = "strongpass123"  # pragma: allowlist secret


class EmailDomainParsingTests(SimpleTestCase):
    """`email_domain` is the single place a domain is extracted. Everything
    it can't confidently reduce to a domain comes back as "" and is refused
    by both rules."""

    def test_extracts_and_lowercases_the_domain(self):
        self.assertEqual(email_domain("Jane.Doe@Gmail.COM"), "gmail.com")

    def test_surrounding_whitespace_is_stripped(self):
        """`" jane@gmail.com "` used to yield the domain `"gmail.com "`,
        which missed the consumer-domain lookup and classified as business."""
        self.assertEqual(email_domain("  jane@gmail.com  "), "gmail.com")
        self.assertTrue(is_personal_email("  jane@gmail.com  "))

    def test_trailing_dot_is_stripped(self):
        """ "gmail.com." is a legal fully-qualified form and must not dodge
        the lookup."""
        self.assertEqual(email_domain("jane@gmail.com."), "gmail.com")
        self.assertTrue(is_personal_email("jane@gmail.com."))

    def test_address_without_an_at_sign_is_not_a_domain(self):
        """The old split returned the whole string here, so "gmail.com"
        typed into the wrong box classified as a business domain."""
        self.assertEqual(email_domain("gmail.com"), "")
        self.assertFalse(is_business_email("gmail.com"))
        self.assertFalse(is_personal_email("gmail.com"))

    def test_rejects_malformed_addresses_without_raising(self):
        for value in [
            None,
            "",
            "   ",
            "@gmail.com",
            " @gmail.com",
            "jane@",
            "jane@@gmail.com",
            "jane@school@org.com",
            "jane@localhost",
            "jane@.com",
            "jane@school..org",
            "jane@-school.org",
            "jane@sch ool.org",
            12345,
        ]:
            # None and 12345 are deliberately the wrong type: these helpers
            # take whatever a request body supplied, so the point of the test
            # is that a non-string returns "" rather than raising.
            with self.subTest(value=value):
                self.assertEqual(email_domain(value), "")  # type: ignore[arg-type]
                self.assertFalse(is_business_email(value))  # type: ignore[arg-type]
                self.assertFalse(is_personal_email(value))  # type: ignore[arg-type]

    def test_unicode_domains_normalise_to_punycode(self):
        self.assertEqual(email_domain("jane@schülé.de"), "xn--schl-epa6i.de")


class PersonalEmailTests(SimpleTestCase):
    def test_known_consumer_providers_are_personal(self):
        for email in [
            "jane@gmail.com",
            "jane@yahoo.com",
            "jane@hotmail.com",
            "jane@icloud.com",
            "jane@aol.com",
        ]:
            with self.subTest(email=email):
                self.assertTrue(is_personal_email(email))
                self.assertFalse(is_business_email(email))

    def test_consumer_aliases_and_country_variants_are_personal(self):
        """The whole point of the rewrite. Every one of these classified as
        a BUSINESS address before, so each was a working bypass of the
        school-admin and license-enrollment gates."""
        for email in [
            "jane@googlemail.com",
            "jane@yahoo.co.uk",
            "jane@yahoo.com.ng",
            "jane@hotmail.fr",
            "jane@outlook.co.uk",
            "jane@live.co.uk",
            "jane@proton.me",
            "jane@pm.me",
            "jane@gmx.net",
            "jane@web.de",
            "jane@tutanota.com",
            "jane@me.com",
            "jane@ymail.com",
            "jane@naver.com",
            "jane@comcast.net",
            "jane@btinternet.com",
            "jane@orange.fr",
            "jane@uol.com.br",
        ]:
            with self.subTest(email=email):
                self.assertTrue(is_personal_email(email))
                self.assertFalse(is_business_email(email))

    def test_subdomains_of_consumer_providers_are_personal(self):
        self.assertTrue(is_personal_email("jane@mail.gmail.com"))
        self.assertFalse(is_business_email("jane@mail.gmail.com"))

    def test_an_unrecognised_domain_is_not_personal(self):
        """Positive test, not "absent from a list": an unknown domain is
        refused on the individual track rather than waved through."""
        self.assertFalse(is_personal_email("principal@acme-school.org"))
        self.assertTrue(is_business_email("principal@acme-school.org"))

    def test_a_lookalike_domain_is_not_personal(self):
        """The suffix walk must not match on a shared substring."""
        self.assertFalse(is_personal_email("jane@gmail.com.example.org"))
        self.assertFalse(is_personal_email("jane@notgmail.com"))

    @override_settings(DISALLOWED_EMAIL_DOMAINS=["extra-consumer.example"])
    def test_settings_extend_rather_than_replace_the_builtin_list(self):
        self.assertTrue(is_personal_email("jane@extra-consumer.example"))
        self.assertTrue(is_personal_email("jane@gmail.com"))


class DisposableEmailTests(SimpleTestCase):
    def test_throwaway_providers_are_neither_personal_nor_business(self):
        """These used to classify as business, so a public throwaway address
        could create a school admin."""
        for email in [
            "jane@mailinator.com",
            "jane@guerrillamail.com",
            "jane@10minutemail.com",
            "jane@temp-mail.org",
            "jane@yopmail.com",
            "jane@trashmail.com",
        ]:
            with self.subTest(email=email):
                self.assertTrue(is_disposable_email(email))
                self.assertFalse(is_business_email(email))
                self.assertFalse(is_personal_email(email))

    @override_settings(DISPOSABLE_EMAIL_DOMAINS=["burner.example"])
    def test_settings_can_add_disposable_domains(self):
        self.assertTrue(is_disposable_email("jane@burner.example"))
        self.assertFalse(is_business_email("jane@burner.example"))


class BusinessAllowlistTests(SimpleTestCase):
    """ALLOWED_BUSINESS_EMAIL_DOMAINS used to be a `None` constant nothing
    read. Set it, and business email means "one of these" -- a deployment
    that only serves known schools."""

    @override_settings(ALLOWED_BUSINESS_EMAIL_DOMAINS=["acme-school.org"])
    def test_only_allowlisted_domains_are_business(self):
        self.assertTrue(is_business_email("principal@acme-school.org"))
        self.assertTrue(is_business_email("principal@staff.acme-school.org"))
        self.assertFalse(is_business_email("principal@other-school.org"))
        self.assertFalse(is_business_email("jane@gmail.com"))

    def test_empty_allowlist_means_anything_not_consumer_or_disposable(self):
        self.assertTrue(is_business_email("principal@other-school.org"))


class ExemptDomainTests(SimpleTestCase):
    def test_nothing_is_exempt_by_default(self):
        """yopmail.com sat in this list permanently, and the exemption opens
        BOTH gates -- so anyone could mint a school admin on a public
        throwaway address."""
        self.assertFalse(is_exempt_email_domain("qa@yopmail.com"))

    @override_settings(EXEMPT_EMAIL_DOMAINS=["yopmail.com"])
    def test_an_environment_can_opt_in(self):
        self.assertTrue(is_exempt_email_domain("qa@yopmail.com"))
        self.assertTrue(is_exempt_email_domain("qa@sub.yopmail.com"))
        self.assertFalse(is_exempt_email_domain("jane@gmail.com"))


class SerializerEnforcementTests(TestCase):
    """The rules as the API applies them."""

    def make_user(self, email, user_type=UserTypes.TEACHER, **kwargs):
        return User.objects.create_user(
            email=email,
            password=PASSWORD,
            first_name="Test",
            last_name="User",
            user_type=user_type,
            is_active=True,
            **kwargs,
        )

    def test_teacher_may_not_register_on_a_consumer_alias_gap(self):
        """A teacher on yahoo.co.uk is on a personal address and belongs on
        the individual track -- previously it read as business and was
        rejected."""
        serializer = CustomUserSerializer(
            data={
                "email": "jane@yahoo.co.uk",
                "password": PASSWORD,
                "first_name": "Jane",
                "last_name": "Doe",
            }
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_teacher_on_a_business_email_is_rejected(self):
        serializer = CustomUserSerializer(
            data={
                "email": "principal@acme-school.org",
                "password": PASSWORD,
                "first_name": "Jane",
                "last_name": "Doe",
            }
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn("Business emails are not allowed", str(serializer.errors))

    def test_teacher_on_a_disposable_email_is_rejected(self):
        serializer = CustomUserSerializer(
            data={
                "email": "jane@mailinator.com",
                "password": PASSWORD,
                "first_name": "Jane",
                "last_name": "Doe",
            }
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn("Business emails are not allowed", str(serializer.errors))

    def test_school_admin_may_not_move_to_a_consumer_alias(self):
        """proton.me and googlemail.com are personal addresses; the school
        admin gate used to accept both."""
        admin = self.make_user("admin@acme-school.org", UserTypes.SCHOOL_ADMIN)

        for email in ["admin@proton.me", "admin@googlemail.com", "admin@yahoo.co.uk"]:
            with self.subTest(email=email):
                serializer = CustomUserSerializer(
                    admin, data={"email": email}, partial=True
                )
                self.assertFalse(serializer.is_valid())
                self.assertIn("Personal emails are not allowed", str(serializer.errors))

    def test_school_admin_may_not_move_to_a_disposable_email(self):
        admin = self.make_user("admin@acme-school.org", UserTypes.SCHOOL_ADMIN)

        serializer = CustomUserSerializer(
            admin, data={"email": "admin@mailinator.com"}, partial=True
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn("Personal emails are not allowed", str(serializer.errors))

    def test_changing_user_type_re_checks_the_email(self):
        """The hole: the guard only fired on creation or on an email change,
        so promoting a teacher on a personal address to SCHOOL_ADMIN left a
        school admin sitting on a personal mailbox."""
        teacher = self.make_user("jane@gmail.com", UserTypes.TEACHER)

        serializer = CustomUserSerializer(
            teacher, data={"user_type": UserTypes.SCHOOL_ADMIN}, partial=True
        )
        # user_type is read-only without a super admin in context, so drive
        # validate() directly with the transition it would perform.
        serializer.fields["user_type"].read_only = False

        self.assertFalse(serializer.is_valid())
        self.assertIn("Personal emails are not allowed", str(serializer.errors))

    def test_changing_user_type_the_other_way_re_checks_too(self):
        admin = self.make_user("admin@acme-school.org", UserTypes.SCHOOL_ADMIN)

        serializer = CustomUserSerializer(
            admin, data={"user_type": UserTypes.TEACHER}, partial=True
        )
        serializer.fields["user_type"].read_only = False

        self.assertFalse(serializer.is_valid())
        self.assertIn("Business emails are not allowed", str(serializer.errors))

    def test_system_generated_student_address_is_not_dragged_into_the_rules(self):
        """@student.local placeholders are not mailboxes anyone owns and are
        neither personal nor business. A user_type change must not start
        failing on them."""
        student = self.make_user("jane.doe.a1b2@student.local", UserTypes.STUDENT)

        serializer = CustomUserSerializer(
            student, data={"user_type": UserTypes.TEACHER}, partial=True
        )
        serializer.fields["user_type"].read_only = False

        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_unrelated_update_does_not_re_run_the_rules(self):
        """A teacher already on a business address (e.g. license-invited)
        must still be able to edit their profile."""
        teacher = self.make_user("teacher@acme-school.org", UserTypes.TEACHER)

        serializer = CustomUserSerializer(
            teacher, data={"first_name": "Renamed"}, partial=True
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
