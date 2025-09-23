"""Tests for dashboard URL resolution in navigation templates."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from app import models


class DashboardNavigationURLTests(TestCase):
    """Ensure the dashboard link points to the user-specific restaurant."""

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="owner", email="owner@example.com", password="testpass123"
        )
        self.account = models.Account.objects.create(name="Test Account")
        self.restaurant = models.Restaurant.objects.create(
            account=self.account,
            name="Test Restaurant",
            location_text="Test City",
        )
        models.Membership.objects.create(
            account=self.account,
            user=self.user,
            role=models.Membership.Role.OWNER,
        )

    def test_marketing_layout_uses_dashboard_url_with_restaurant(self):
        self.client.login(username="owner", password="testpass123")
        response = self.client.get(reverse("home"))
        expected_url = reverse("dashboard", args=[self.restaurant.id])
        self.assertContains(response, f'href="{expected_url}"')

    def test_app_layout_uses_dashboard_url_with_restaurant(self):
        self.client.login(username="owner", password="testpass123")
        response = self.client.get(reverse("concepts"))
        expected_url = reverse("dashboard", args=[self.restaurant.id])
        self.assertContains(response, f'href="{expected_url}"')
