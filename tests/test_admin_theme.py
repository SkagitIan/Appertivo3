from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from app import models


class AdminThemeTests(TestCase):
    def setUp(self):
        self.user_model = get_user_model()

    def _login_superuser(self, username: str, email: str) -> None:
        user = self.user_model.objects.create_superuser(
            username=username,
            email=email,
            password="pass12345",
        )
        self.client.force_login(user)

    def test_admin_dashboard_groups_displayed(self):
        self._login_superuser("admin1", "admin@example.com")

        response = self.client.get(reverse("admin:index"))

        self.assertEqual(response.status_code, 200)
        nav_titles = [group["title"] for group in response.context["admin_nav_groups"]]
        self.assertIn("Restaurant Data", nav_titles)
        self.assertIn("Content & Ideas", nav_titles)

    def test_admin_global_search_returns_results(self):
        self._login_superuser("admin2", "admin2@example.com")

        account = models.Account.objects.create(name="Searchable Group")
        models.Restaurant.objects.create(
            account=account,
            name="Sunset Bistro",
            location_text="Portland",
        )

        response = self.client.get(reverse("admin:global-search"), {"q": "Sunset"})

        self.assertEqual(response.status_code, 200)
        rendered = response.content.decode()
        self.assertIn("Search results", rendered)
        self.assertIn("Sunset Bistro", rendered)
