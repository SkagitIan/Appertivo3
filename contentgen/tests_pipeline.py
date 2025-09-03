from unittest.mock import patch, MagicMock
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


class AdminGenerateTests(TestCase):
    """Ensure the admin generate view triggers the pipeline."""

    @patch("contentgen.pipeline.save_article")
    def test_generate_view_calls_save_article(self, mock_save):
        mock_save.return_value = (MagicMock(pk=1), {})
        User = get_user_model()
        admin_user = User.objects.create_superuser("admin", "admin@example.com", "pw")
        self.client.force_login(admin_user)

        response = self.client.post(reverse("admin:contentgen_article_generate"), {"topic_hint": "Test"})

        mock_save.assert_called_once_with("Test")
        self.assertEqual(response.status_code, 302)
