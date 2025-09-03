from datetime import timedelta
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from app.models import Special


class SpecialFormPartialTests(TestCase):
    """Tests for the special form partial template."""

    def setUp(self):
        self.user = User.objects.create_user(username="owner", password="pw")
        self.client.login(username="owner", password="pw")

    def _create_special(self):
        return Special.objects.create(
            user=self.user,
            title="Deal",
            description="Desc",
            price="10.00",
            start_date=timezone.now() - timedelta(days=1),
            end_date=timezone.now() + timedelta(days=1),
            cta_type="web",
            cta_url="",
            cta_phone="",
            status="active",
        )

    def test_create_partial_has_action(self):
        url = reverse("special_form_create_partial")
        response = self.client.get(url)
        self.assertContains(response, f'action="{reverse("create_special")}"')

    def test_edit_partial_has_action(self):
        special = self._create_special()
        url = reverse("special_form_edit_partial", args=[special.id])
        response = self.client.get(url)
        self.assertContains(response, f'action="{reverse("special_edit", args=[special.id])}"')

    def test_create_partial_has_recurrence_fields(self):
        url = reverse("special_form_create_partial")
        response = self.client.get(url)
        self.assertContains(response, 'name="byday"')
