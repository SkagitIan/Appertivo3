from django.test import TestCase
from django.urls import reverse
from django.contrib.auth.models import User
from django.utils import timezone

from app.models import UserProfile, Subscription


class BillingTests(TestCase):
    """Tests for the billing page and subscription flow."""

    def setUp(self):
        self.user = User.objects.create_user(username="test@example.com", password="pass")
        UserProfile.objects.create(user=self.user, restaurant_name="Testaurant")

    def test_billing_page_displays_single_plan(self):
        self.client.login(username="test@example.com", password="pass")
        response = self.client.get(reverse("billing"))
        self.assertContains(response, "$199")
        self.assertContains(response, "14-day free trial")

    def test_cancel_subscription_sets_cancelled_status(self):
        profile = UserProfile.objects.get(user=self.user)
        subscription = Subscription.objects.create(
            user=self.user,
            stripe_subscription_id="sub_123",
            signup_date=timezone.now(),
            trial_end_date=timezone.now() + timezone.timedelta(days=14),
            subscription_status="active",
        )
        profile.subscription_tier = "pro"
        profile.save()
        self.client.login(username="test@example.com", password="pass")
        response = self.client.post(reverse("cancel_subscription"))
        self.assertRedirects(response, reverse("billing"))
        subscription.refresh_from_db()
        self.assertEqual(subscription.subscription_status, "cancelled")
        profile.refresh_from_db()
        self.assertEqual(profile.subscription_tier, "free")
