from decimal import Decimal
from datetime import timedelta
from django.utils import timezone
from django.test import TestCase, override_settings
from django.contrib.auth.models import User
from unittest.mock import patch

from app.models import Connection, Special, SpecialMetrics
from app.integrations.google import publish_special, fetch_post_metrics


class GoogleMetricsTests(TestCase):
    """Tests for Google post metrics integration."""

    @override_settings(GOOGLE_API_KEY="key")
    @patch("app.integrations.google.requests.post")
    def test_publish_special_saves_post_name(self, mock_post):
        user = User.objects.create_user(username="owner", password="pw")
        Connection.objects.create(
            user=user,
            platform="google_business",
            is_connected=True,
            settings={"access_token": "tok", "account_id": "acc", "location_id": "loc"},
        )
        special = Special.objects.create(
            user=user,
            title="Deal",
            description="Desc",
            price=Decimal("5.00"),
            cta_url="https://example.com",
            start_date=timezone.now(),
            end_date=timezone.now() + timedelta(days=1),
        )
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {
            "name": "accounts/acc/locations/loc/localPosts/123"
        }
        publish_special(special)
        special.refresh_from_db()
        self.assertEqual(
            special.google_post_name,
            "accounts/acc/locations/loc/localPosts/123",
        )

    @override_settings(GOOGLE_API_KEY="key")
    @patch("app.integrations.google.requests.post")
    def test_fetch_post_metrics_stores_results(self, mock_post):
        user = User.objects.create_user(username="owner2", password="pw")
        Connection.objects.create(
            user=user,
            platform="google_business",
            is_connected=True,
            settings={"access_token": "tok", "account_id": "acc", "location_id": "loc"},
        )
        special = Special.objects.create(
            user=user,
            title="Deal",
            description="Desc",
            price=Decimal("5.00"),
            cta_url="https://example.com",
            start_date=timezone.now(),
            end_date=timezone.now() + timedelta(days=1),
            google_post_name="accounts/acc/locations/loc/localPosts/123",
            status="active",
        )
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {
            "localPostMetrics": [
                {
                    "localPostName": "accounts/acc/locations/loc/localPosts/123",
                    "metricValues": [
                        {"metric": "LOCAL_POST_VIEWS", "value": 5},
                        {"metric": "CALL_TO_ACTION_CLICKS", "value": 2},
                    ],
                }
            ]
        }
        fetch_post_metrics([special])
        metrics = SpecialMetrics.objects.get(special=special)
        self.assertEqual(metrics.views, 5)
        self.assertEqual(metrics.cta_clicks, 2)
        url = mock_post.call_args[0][0]
        self.assertIn("localPosts:reportInsights", url)
