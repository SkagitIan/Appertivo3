from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


class AdminDashboardLinkTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser(
            username="admin", email="admin@example.com", password="password"
        )

    def test_admin_index_renders_with_dashboard_link(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("admin:index"))

        self.assertEqual(response.status_code, 200)
        self.assertIn("articles_dashboard", response.context)
        self.assertIn(
            reverse("admin:articles_admin_dashboard"),
            response.context["articles_dashboard"],
        )
