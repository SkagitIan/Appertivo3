from django.test import TestCase, override_settings
from django.urls import reverse


@override_settings(DEBUG=False)
class NotFoundPageTests(TestCase):
    """Ensure the custom 404 page renders with helpful messaging."""

    def test_custom_404_template_used(self):
        response = self.client.get("/definitely-not-here/")

        self.assertEqual(response.status_code, 404)
        self.assertTemplateUsed(response, "404.html")
        self.assertContains(response, "Let's get you back on the menu")
        self.assertContains(response, reverse("home"))
        self.assertContains(response, "Contact support")
