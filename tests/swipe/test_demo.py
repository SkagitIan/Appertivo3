from django.test import TestCase
from django.urls import reverse

from app.models import Account, Restaurant


class SwipeDemoSmokeTests(TestCase):
    def setUp(self):
        account = Account.objects.create(name="Demo Account")
        Restaurant.objects.create(account=account, name="Demo Bistro")

    def test_paid_and_demo_views_include_slide_menu(self):
        paid_response = self.client.get(reverse("swipe:home"))
        demo_response = self.client.get(reverse("swipe:swipe-demo"))

        self.assertEqual(paid_response.status_code, 200)
        self.assertEqual(demo_response.status_code, 200)

        menu_label = "Favorites Archive"
        self.assertIn(menu_label, paid_response.content.decode())
        self.assertIn(menu_label, demo_response.content.decode())
