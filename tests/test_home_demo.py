from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from app import models


class HomeDemoCardWithDataTests(TestCase):
    def setUp(self):
        self.demo_user = User.objects.create_user(
            id=17,
            username="demo",
            password="pw",
            email="demo@example.com",
        )
        self.account = models.Account.objects.create(name="Demo Account")
        models.Membership.objects.create(account=self.account, user=self.demo_user)
        self.restaurant = models.Restaurant.objects.create(
            account=self.account,
            name="Demo Bistro",
            location_text="Seattle, WA",
        )

        self.concept_run = models.IdeationRun.objects.create(
            restaurant=self.restaurant,
            initiated_by_user=self.demo_user,
            type=models.IdeationRun.RunType.CONCEPTS,
            model_name="gpt-4",
            temperature=0.2,
            classic_creative=50,
            context_snapshot={},
            status=models.IdeationRun.Status.SUCCEEDED,
        )
        self.concept = models.Concept.objects.create(
            restaurant=self.restaurant,
            ideation_run=self.concept_run,
            name="Charred Citrus Salmon",
            subtitle="Honeyed fennel & burnt orange glaze",
            reasoning="Keeps grill marks intact while layering bright citrus aromatics for patio season.",
            tags=["Seafood", "Summer"],
            rank_order=1,
            sketch_image_url="https://example.com/sketch.jpg",
        )
        self.concept_favorite = models.FavoriteConcept.objects.create(
            user=self.demo_user,
            concept=self.concept,
            favorited_at=timezone.now(),
        )

        self.dish_run = models.IdeationRun.objects.create(
            restaurant=self.restaurant,
            initiated_by_user=self.demo_user,
            type=models.IdeationRun.RunType.DISHES,
            model_name="gpt-4",
            temperature=0.3,
            classic_creative=40,
            context_snapshot={},
            parent_concept=self.concept,
            status=models.IdeationRun.Status.SUCCEEDED,
        )
        self.dish = models.DishIdea.objects.create(
            restaurant=self.restaurant,
            ideation_run=self.dish_run,
            parent_concept=self.concept,
            title="Charred Citrus Salmon Plate",
            description="Grilled king salmon with fennel slaw, burnt orange glaze, and smoked sea salt.",
            ingredient_names=["salmon", "fennel", "orange"],
            category_tags=["Seafood", "Entrée", "Summer"],
        )
        self.dish_favorite = models.FavoriteDish.objects.create(
            user=self.demo_user,
            dish=self.dish,
            favorited_at=timezone.now(),
        )

        asset = models.Asset.objects.create(
            kind=models.Asset.Kind.IMAGE,
            storage_key="demo/salmon",
            public_url="https://example.com/dish.jpg",
        )
        models.Enhancement.objects.create(
            dish=self.dish,
            status=models.Enhancement.Status.SUCCEEDED,
            image_asset=asset,
            suggested_price_cents=2800,
            currency="USD",
            model_name="vision-pro",
            started_at=timezone.now(),
            finished_at=timezone.now(),
        )

    def test_home_view_uses_demo_favorites(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)

        concept = response.context["demo_concept"]
        self.assertIsNotNone(concept)
        self.assertEqual(concept.name, "Charred Citrus Salmon")
        self.assertTrue(getattr(concept, "is_favorited_for_user", False))

        dish = response.context["demo_dish"]
        self.assertIsNotNone(dish)
        self.assertTrue(getattr(dish, "is_favorited", False))
        self.assertEqual(response.context["demo_restaurant"].name, "Demo Bistro")
        self.assertEqual(
            response.context["demo_concept_favorite"].concept_id,
            self.concept.id,
        )
        self.assertEqual(
            response.context["demo_dish_favorite"].dish_id,
            self.dish.id,
        )
        self.assertEqual(response.context["demo_user_id"], 17)


class HomeDemoCardWithoutDataTests(TestCase):
    def test_home_view_without_demo_data(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["demo_concept"])
        self.assertIsNone(response.context["demo_dish"])
