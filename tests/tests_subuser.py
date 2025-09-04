from django.test import TestCase
from django.urls import reverse
from django.contrib.auth.models import User

from app.models import SubUserProfile


class SubuserDashboardTests(TestCase):
    """Tests for subuser dashboard behavior."""

    def setUp(self):
        self.owner = User.objects.create_user(username="owner", password="pass123")

    def test_owner_sees_add_subuser_card(self):
        """Dashboard should show add subuser card for account owners."""
        self.client.login(username="owner", password="pass123")
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "Add Subuser")

    def test_subuser_hides_billing(self):
        """Billing link should be hidden for subusers."""
        sub_user = User.objects.create_user(username="sub", password="pass123")
        SubUserProfile.objects.create(owner=self.owner, user=sub_user)
        self.client.login(username="sub", password="pass123")
        response = self.client.get(reverse("dashboard"))
        self.assertNotContains(response, "Billing")
