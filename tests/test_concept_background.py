from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from app import llm, models


@override_settings(SECURE_SSL_REDIRECT=False)
class ConceptBackgroundViewTests(TestCase):
    """Verify the lazy concept background view returns a usable image URL."""

    def setUp(self) -> None:
        self.user = User.objects.create_user("user@example.com", password="pw")
        self.account = models.Account.objects.create(name="Account")
        models.Membership.objects.create(
            account=self.account,
            user=self.user,
            role=models.Membership.Role.OWNER,
        )
        self.restaurant = models.Restaurant.objects.create(
            account=self.account,
            name="Test Restaurant",
            location_text="Test City",
        )
        models.RestaurantSettings.objects.create(restaurant=self.restaurant)
        self.client.login(username="user@example.com", password="pw")

        self.run = models.IdeationRun.objects.create(
            restaurant=self.restaurant,
            initiated_by_user=self.user,
            type=models.IdeationRun.RunType.CONCEPTS,
            model_name="mock",
            temperature=0,
            classic_creative=50,
            context_snapshot={},
            status=models.IdeationRun.Status.SUCCEEDED,
        )
        self.concept = models.Concept.objects.create(
            restaurant=self.restaurant,
            ideation_run=self.run,
            name="Evening Jazz",
            subtitle="Warm vibes with live music.",
            rank_order=1,
        )

    def test_returns_placeholder_image_without_gemini_key(self) -> None:
        url = reverse("concept-background", args=[self.concept.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("concept-card-background concept-card-background--loaded", content)
        self.assertIn(llm.DEFAULT_CONCEPT_IMAGE_URL, content)

    def test_reuses_existing_background_image(self) -> None:
        self.concept.sketch_image_url = "https://stored.example/sketch.png"
        self.concept.save(update_fields=["sketch_image_url"])

        url = reverse("concept-background", args=[self.concept.id])
        with mock.patch("app.llm.generate_concept_sketch") as mock_generate:
            response = self.client.get(url)

        mock_generate.assert_not_called()
        content = response.content.decode()
        self.assertIn("https://stored.example/sketch.png", content)


@override_settings(SECURE_SSL_REDIRECT=False)
class ConceptFavoriteBackgroundTests(TestCase):
    """Ensure concept background images generate when favorited."""

    def setUp(self) -> None:
        self.user = User.objects.create_user("user2@example.com", password="pw")
        self.account = models.Account.objects.create(name="Another Account")
        models.Membership.objects.create(
            account=self.account,
            user=self.user,
            role=models.Membership.Role.OWNER,
        )
        self.restaurant = models.Restaurant.objects.create(
            account=self.account,
            name="Favorite Restaurant",
            location_text="Sample City",
        )
        models.RestaurantSettings.objects.create(restaurant=self.restaurant)
        self.client.login(username="user2@example.com", password="pw")

        run = models.IdeationRun.objects.create(
            restaurant=self.restaurant,
            initiated_by_user=self.user,
            type=models.IdeationRun.RunType.CONCEPTS,
            model_name="mock",
            temperature=0,
            classic_creative=50,
            context_snapshot={},
            status=models.IdeationRun.Status.SUCCEEDED,
        )
        self.concept = models.Concept.objects.create(
            restaurant=self.restaurant,
            ideation_run=run,
            name="Sunset Garden",
            subtitle="Botanical cocktails on a rooftop terrace.",
            rank_order=1,
        )



