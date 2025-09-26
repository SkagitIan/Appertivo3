from django.test import TestCase
from django.urls import reverse


class ContactPageTests(TestCase):
    """Tests for the public contact page."""

    def test_contact_page_renders_with_sections(self):
        response = self.client.get(reverse("contact"))
        self.assertContains(response, "Contact Us")
        for address in [
            "help@appertivo.com",
            "hello@appertivo.com",
            "press@appertivo.com",
            "demos@appertivo.com",
        ]:
            with self.subTest(address=address):
                self.assertContains(response, address)

    def test_privacy_page_renders(self):
        response = self.client.get(reverse("privacy"))
        self.assertContains(response, "Privacy Policy")
        self.assertContains(response, "We take the privacy of every restaurant seriously.")

    def test_terms_page_renders(self):
        response = self.client.get(reverse("terms"))
        self.assertContains(response, "Terms of Service")
        self.assertContains(response, "You own every concept")
