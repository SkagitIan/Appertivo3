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

    def test_add_subuser_requires_email(self):
        """Creating a subuser without email should fail."""
        self.client.login(username="owner", password="pass123")
        response = self.client.post(
            reverse("add_subuser"),
            {
                "username": "sub",
                "password1": "pass12345",
                "password2": "pass12345",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(User.objects.count(), 1)

    def test_dashboard_lists_subuser_email(self):
        """Dashboard should display subuser email."""
        self.client.login(username="owner", password="pass123")
        self.client.post(
            reverse("add_subuser"),
            {
                "username": "sub",
                "email": "sub@example.com",
                "password1": "pass12345",
                "password2": "pass12345",
            },
        )
        self.assertTrue(
            User.objects.filter(username="sub", email="sub@example.com").exists()
        )
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "sub@example.com")
