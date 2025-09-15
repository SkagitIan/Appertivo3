import json

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from app import models


@override_settings(SECURE_SSL_REDIRECT=False)
class SignupViewTests(TestCase):
    """Tests for the signup API endpoint."""

    def test_signup_creates_records(self):
        """Posting to signup should create user and related objects."""
        payload = {
            "email": "owner@example.com",
            "password": "pw",
            "restaurant_name": "Tasty Place",
            "location": "City, State",
            "menu_url": "http://example.com/menu",
        }
        response = self.client.post(
            reverse("api-signup"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(User.objects.filter(username="owner@example.com").exists())
        account = models.Account.objects.get()
        self.assertTrue(
            models.Membership.objects.filter(account=account, user__username="owner@example.com").exists()
        )
        restaurant = models.Restaurant.objects.get()
        self.assertEqual(restaurant.name, "Tasty Place")
        self.assertEqual(restaurant.location_text, "City, State")
        self.assertEqual(restaurant.primary_menu_url, "http://example.com/menu")
        self.assertTrue(models.OutscraperPayload.objects.filter(restaurant=restaurant).exists())
