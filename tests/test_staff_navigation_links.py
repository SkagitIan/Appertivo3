"""Tests for staff-only dashboard navigation links."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from app import models


class StaffNavigationLinkTests(TestCase):
    """Ensure internal navigation links only appear for staff users."""

    def setUp(self) -> None:
        user_model = get_user_model()
        self.account = models.Account.objects.create(name="Test Account")
        self.restaurant = models.Restaurant.objects.create(
            account=self.account,
            name="Test Bistro",
            location_text="New York",
        )
        self.staff_user = user_model.objects.create_user(
            username="staffer",
            email="staff@example.com",
            password="password123",
            is_staff=True,
        )
        self.member_user = user_model.objects.create_user(
            username="member",
            email="member@example.com",
            password="password123",
        )
        models.Membership.objects.create(account=self.account, user=self.staff_user)
        models.Membership.objects.create(account=self.account, user=self.member_user)

    def test_staff_user_sees_internal_links(self) -> None:
        """Staff users should see the leads, articles, and dashboard links."""

        self.client.force_login(self.staff_user)
        response = self.client.get(reverse("menus"))
        self.assertContains(response, f'href="{reverse("lead-dashboard")}"')
        self.assertContains(response, f'href="{reverse("articles:staff_dashboard")}"')
        self.assertContains(response, f'href="{reverse("dashboard:overview")}"')

    def test_member_does_not_see_internal_links(self) -> None:
        """Non-staff members should not see staff-only navigation links."""

        self.client.force_login(self.member_user)
        response = self.client.get(reverse("menus"))
        self.assertNotContains(response, f'href="{reverse("lead-dashboard")}"')
        self.assertNotContains(response, f'href="{reverse("articles:staff_dashboard")}"')
        self.assertNotContains(response, f'href="{reverse("dashboard:overview")}"')
