from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from appertivo.leads.models import Lead, LeadRun


class LeadDashboardPanelViewTests(TestCase):
    def setUp(self) -> None:
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="lead-panel@example.com",
            email="lead-panel@example.com",
            password="password123",
        )

    def test_panel_fragment_renders_new_leads(self) -> None:
        run = LeadRun.objects.create(
            city="Austin, TX",
            status=LeadRun.Status.READY,
            expected_leads=1,
        )
        Lead.objects.create(
            run=run,
            name="Panel Bistro",
            email="hello@panel.test",
            city="Austin, TX",
        )

        self.client.force_login(self.user)
        response = self.client.get(reverse("lead-dashboard-panel"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "leads/_dashboard_panel.html")
        html = response.content.decode()
        self.assertIn("Panel Bistro", html)
        self.assertIn(f"Run {run.pk}", html)
        self.assertIn('data-dashboard-polling="off"', html)
