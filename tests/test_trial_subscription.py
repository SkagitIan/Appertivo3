from django.test import TestCase
from django.urls import reverse
from django.contrib.auth.models import User
from django.utils import timezone
from unittest.mock import patch
from datetime import timedelta

from app.models import UserProfile, Subscription


class TrialSubscriptionTests(TestCase):
    def setUp(self):
        self.email = "trial@example.com"
        self.password = "pass"
        self.restaurant = "TrialResto"
        self.location = "City"

    @patch("app.views.stripe.Subscription.create")
    @patch("app.views.stripe.Customer.create")
    def test_registration_creates_trial_subscription(self, mock_customer_create, mock_sub_create):
        mock_customer_create.return_value = {"id": "cus_123"}
        mock_sub_create.return_value = {"id": "sub_123"}

        self.client.post(
            reverse("register"),
            {
                "email": self.email,
                "password": self.password,
                "restaurant_name": self.restaurant,
                "location": self.location,
            },
        )

        user = User.objects.get(email=self.email)
        subscription = Subscription.objects.get(user=user)
        self.assertEqual(subscription.subscription_status, "trialing")
        self.assertAlmostEqual(subscription.trial_end_date, subscription.signup_date + timedelta(days=14), delta=timedelta(seconds=1))

    def test_dashboard_redirects_when_trial_expired(self):
        user = User.objects.create_user(username=self.email, email=self.email, password=self.password)
        UserProfile.objects.create(user=user, restaurant_name=self.restaurant)
        Subscription.objects.create(
            user=user,
            stripe_subscription_id="sub_123",
            signup_date=timezone.now() - timedelta(days=15),
            trial_end_date=timezone.now() - timedelta(days=1),
            subscription_status="trialing",
        )
        self.client.login(username=self.email, password=self.password)
        response = self.client.get(reverse("dashboard"))
        self.assertRedirects(response, reverse("billing"))
