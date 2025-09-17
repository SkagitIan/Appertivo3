from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from app import llm, models


class EnhancedModeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="chef@example.com", email="chef@example.com", password="pw"
        )
        self.account = models.Account.objects.create(name="Test Account")
        models.Membership.objects.create(
            account=self.account, user=self.user, role=models.Membership.Role.OWNER
        )
        self.restaurant = models.Restaurant.objects.create(
            account=self.account,
            name="Flavor Town",
            location_text="City",
            context_json={"name": "Flavor Town"},
        )
        settings = models.RestaurantSettings.objects.create(restaurant=self.restaurant)
        settings.save()

        self.menu_version = models.MenuVersion.objects.create(
            restaurant=self.restaurant,
            source_kind=models.MenuVersion.SourceKind.URL_SCRAPE,
            raw_markdown="Soup $10",
            status=models.MenuVersion.Status.SUCCEEDED,
        )
        self.restaurant.active_menu_version = self.menu_version
        self.restaurant.save(update_fields=["active_menu_version"])

        self.concept_run = models.IdeationRun.objects.create(
            restaurant=self.restaurant,
            initiated_by_user=self.user,
            type=models.IdeationRun.RunType.CONCEPTS,
            model_name="gpt",
            temperature=0.5,
            classic_creative=50,
            context_snapshot={},
            status=models.IdeationRun.Status.SUCCEEDED,
        )
        self.concept = models.Concept.objects.create(
            restaurant=self.restaurant,
            ideation_run=self.concept_run,
            name="Comfort Classics",
            rank_order=1,
        )
        self.dish_run = models.IdeationRun.objects.create(
            restaurant=self.restaurant,
            initiated_by_user=self.user,
            type=models.IdeationRun.RunType.DISHES,
            model_name="gpt",
            temperature=0.5,
            classic_creative=50,
            context_snapshot={},
            parent_concept=self.concept,
            status=models.IdeationRun.Status.SUCCEEDED,
        )
        self.dish = models.DishIdea.objects.create(
            restaurant=self.restaurant,
            ideation_run=self.dish_run,
            parent_concept=self.concept,
            title="Smoked Tomato Bisque",
            description="Slow-roasted tomatoes finished with cream.",
            ingredient_names=["tomato", "cream"],
            category_tags=["soup"],
        )

    def test_favoriting_creates_enhancement(self):
        self.client.force_login(self.user)
        url = reverse("dish_favorite", args=[self.dish.id]) + "?context=grid"
        response = self.client.post(url, HTTP_HX_REQUEST="true", secure=True)

        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn("bg-red-600", html)
        self.assertIn("Smoked Tomato Bisque", html)

        enhancement = models.Enhancement.objects.get(dish=self.dish)
        self.assertEqual(enhancement.suggested_price_cents, llm.DEFAULT_PRICE_CENTS)
        self.assertIsNotNone(enhancement.image_asset)
        self.assertTrue(
            models.FavoriteDish.objects.filter(user=self.user, dish=self.dish).exists()
        )

    def test_unfavorite_in_favorites_context_removes_card(self):
        self.client.force_login(self.user)
        url = reverse("dish_favorite", args=[self.dish.id]) + "?context=grid"
        self.client.post(url, HTTP_HX_REQUEST="true", secure=True)

        remove_url = reverse("dish_favorite", args=[self.dish.id]) + "?context=favorites"
        response = self.client.post(remove_url, HTTP_HX_REQUEST="true", secure=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode(), "")
        self.assertFalse(
            models.FavoriteDish.objects.filter(user=self.user, dish=self.dish).exists()
        )
        self.assertFalse(models.Enhancement.objects.filter(dish=self.dish).exists())
        self.assertFalse(models.Asset.objects.exists())
