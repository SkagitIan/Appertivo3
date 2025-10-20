from django.test import TestCase
from django.urls import reverse

from app.models import Account, Restaurant, RestaurantSettings


class CreativeBalanceTests(TestCase):
    def setUp(self):
        self.account = Account.objects.create(name="Test Account")
        self.restaurant = Restaurant.objects.create(
            account=self.account,
            name="Balance Bistro",
            location_text="123 Flavor Street",
        )

    def test_home_view_includes_restaurant_settings(self):
        RestaurantSettings.objects.create(
            restaurant=self.restaurant, classic_creative_slider=77
        )

        response = self.client.get(
            reverse("swipe:home"),
            {"restaurant_id": str(self.restaurant.id)},
        )

        self.assertEqual(response.status_code, 200)
        context = response.context_data
        self.assertEqual(
            context["restaurant_settings"].classic_creative_slider,
            77,
        )
        update_url = reverse("update_creativity", args=[self.restaurant.id])
        self.assertEqual(context["update_creativity_url"], update_url)
        self.assertIn(f'hx-post="{update_url}"', response.content.decode())

    def test_creativity_value_persists_after_update(self):
        self.assertFalse(
            RestaurantSettings.objects.filter(restaurant=self.restaurant).exists()
        )

        update_url = reverse("update_creativity", args=[self.restaurant.id])
        response = self.client.post(
            update_url,
            {"classic_creative_slider": 64},
        )

        self.assertEqual(response.status_code, 200)

        response = self.client.get(
            reverse("swipe:home"),
            {"restaurant_id": str(self.restaurant.id)},
        )

        context = response.context_data
        self.assertEqual(
            context["restaurant_settings"].classic_creative_slider,
            64,
        )

    def test_settings_view_renders_creative_balance_control(self):
        RestaurantSettings.objects.create(
            restaurant=self.restaurant, classic_creative_slider=59
        )

        response = self.client.get(
            reverse("swipe:settings"),
            {"restaurant_id": str(self.restaurant.id)},
        )

        self.assertEqual(response.status_code, 200)
        context = response.context_data
        self.assertEqual(
            context["restaurant_settings"].classic_creative_slider,
            59,
        )
        update_url = reverse("update_creativity", args=[self.restaurant.id])
        html = response.content.decode()
        self.assertIn("data-creative-balance", html)
        self.assertIn(f'hx-post="{update_url}"', html)

    def test_demo_views_provide_default_settings(self):
        home_response = self.client.get(reverse("swipe:swipe-demo"))
        settings_response = self.client.get(reverse("swipe:demo_settings"))

        self.assertEqual(home_response.status_code, 200)
        self.assertEqual(settings_response.status_code, 200)

        home_context = home_response.context_data
        settings_context = settings_response.context_data

        self.assertEqual(
            home_context["restaurant_settings"].classic_creative_slider,
            50,
        )
        self.assertEqual(home_context["update_creativity_url"], "#")

        self.assertEqual(
            settings_context["restaurant_settings"].classic_creative_slider,
            50,
        )
        self.assertEqual(settings_context["update_creativity_url"], "#")
