"""Tests for mobile navigation in the dashboard."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


class DashboardMobileNavigationTests(TestCase):
    """Ensure the dashboard mobile menu appears with expected links."""

    def setUp(self):
        User = get_user_model()
        User.objects.create_user(username="tester", password="pass123")

    def test_mobile_menu_contains_links_for_authenticated_user(self):
        self.client.login(username="tester", password="pass123")
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'id="mobile-menu-button"', html=False)
        self.assertContains(response, 'id="mobile-menu"', html=False)
        self.assertContains(response, reverse('dashboard'))
        self.assertContains(response, reverse('specials_list'))
        self.assertContains(response, reverse('widget_setup'))
        self.assertContains(response, reverse('connections'))
        self.assertContains(response, reverse('billing'))
        self.assertContains(response, reverse('logout'))

