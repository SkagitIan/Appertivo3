from django.test import TestCase
from django.urls import reverse
from django.contrib.auth.models import User
from unittest.mock import patch

from app.models import UserProfile


class RegistrationTests(TestCase):
    """Tests for user registration and profile enrichment."""

    def test_registration_requires_location(self):
        """Registration should fail when location is missing."""
        response = self.client.post(reverse('register'), {
            'email': 'test@example.com',
            'password': 'password123',
            'restaurant_name': 'Testaurant',
        })
        self.assertEqual(User.objects.count(), 0)
        self.assertContains(response, 'Location is required')

    @patch('app.views.fetch_place_details')
    def test_profile_enriched_with_google_data(self, mock_fetch):
        mock_fetch.return_value = {
            'google_place_id': 'abc123',
            'formatted_address': '123 Main St',
            'phone_number': '+123456789',
        }

        with patch('app.views.threading.Thread') as mock_thread:
            # Make the thread run the target immediately for test determinism
            def run_immediately(*args, **kwargs):
                target = kwargs.get('target') or args[0]
                t_args = kwargs.get('args') or args[1] if len(args) > 1 else ()
                target(*t_args)
                class Dummy:
                    def start(self_inner):
                        return None
                return Dummy()
            mock_thread.side_effect = run_immediately

            self.client.post(reverse('register'), {
                'email': 'test2@example.com',
                'password': 'password123',
                'restaurant_name': 'Testaurant',
                'location': 'New York',
            })

        profile = UserProfile.objects.get(user__email='test2@example.com')
        self.assertEqual(profile.location, 'New York')
        self.assertEqual(profile.google_place_id, 'abc123')
        self.assertEqual(profile.formatted_address, '123 Main St')
        self.assertEqual(profile.phone_number, '+123456789')
