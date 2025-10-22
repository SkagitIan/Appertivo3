from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from app.models import Account, Membership, Onboarding, Restaurant


class SwipeDemoSmokeTests(TestCase):
    def setUp(self):
        account = Account.objects.create(name="Demo Account")
        Restaurant.objects.create(
            account=account,
            name="Demo Bistro",
            location_text="Demo City",
        )
        user_account = Account.objects.create(name="User Account")
        self.user_restaurant = Restaurant.objects.create(
            account=user_account,
            name="User Bistro",
            location_text="User City",
        )
        User = get_user_model()
        self.user = User.objects.create_user(
            username="demo@example.com",
            email="demo@example.com",
            password="pass1234",
        )
        Membership.objects.create(account=user_account, user=self.user)
        Onboarding.objects.create(
            user=self.user,
            restaurant=self.user_restaurant,
            state=Onboarding.State.COMPLETE,
        )

    def test_paid_and_demo_views_include_slide_menu(self):
        paid_response = self.client.get(reverse("swipe:home"))
        demo_response = self.client.get(reverse("swipe:swipe-demo"))

        login_url = reverse("login")
        self.assertEqual(paid_response.status_code, 302)
        self.assertTrue(paid_response.url.startswith(f"{login_url}?"))
        self.assertEqual(demo_response.status_code, 200)

        self.client.force_login(self.user)
        paid_response = self.client.get(reverse("swipe:home"))
        demo_response = self.client.get(reverse("swipe:swipe-demo"))

        self.assertEqual(paid_response.status_code, 200)
        self.assertEqual(demo_response.status_code, 200)

        menu_label = "Favorites Archive"
        self.assertIn(menu_label, paid_response.content.decode())
        self.assertIn(menu_label, demo_response.content.decode())
