from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from appertivo.leads.models import Lead, LeadRun


class LeadReviewTableTests(TestCase):
    """Ensure the new lead review table renders expected controls."""

    def setUp(self) -> None:
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="lead-table@example.com",
            email="lead-table@example.com",
            password="password123",
        )

    def test_dashboard_shows_review_table(self) -> None:
        run = LeadRun.objects.create(city="Austin, TX", status=LeadRun.Status.READY)
        Lead.objects.create(
            run=run,
            name="Panel Bistro",
            email="hello@panel.test",
            city="Austin, TX",
        )

        self.client.force_login(self.user)
        response = self.client.get(reverse("lead-dashboard"))

        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn("Review &amp; manage leads", html)
        self.assertIn("Approve selected", html)
        self.assertIn("Panel Bistro", html)
