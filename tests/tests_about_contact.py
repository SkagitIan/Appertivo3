from django.test import TestCase
from django.urls import reverse
from django.core import mail


class AboutContactTests(TestCase):
    """Tests for the About and Contact pages."""

    def test_about_page_renders(self):
        response = self.client.get(reverse("about"))
        self.assertContains(response, "About Appertivo")

    def test_contact_form_sends_email(self):
        response = self.client.post(
            reverse("contact"),
            {"name": "Alice", "email": "alice@example.com", "message": "Hi"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertEqual(email.to, ["ian.larsen.1976@gmail.com"])
        self.assertIn("Hi", email.body)
