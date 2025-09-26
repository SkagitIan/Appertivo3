from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from app import models


class TagSearchViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("searcher@example.com", password="pw12345")
        self.account = models.Account.objects.create(name="Primary")
        models.Membership.objects.create(account=self.account, user=self.user)
        self.restaurant = models.Restaurant.objects.create(
            account=self.account,
            name="Testaurant",
            location_text="City",
        )

        concept_run = models.IdeationRun.objects.create(
            restaurant=self.restaurant,
            initiated_by_user=self.user,
            type=models.IdeationRun.RunType.CONCEPTS,
            model_name="model",
            temperature=0,
            classic_creative=50,
            context_snapshot={},
            status=models.IdeationRun.Status.SUCCEEDED,
        )
        self.concept = models.Concept.objects.create(
            restaurant=self.restaurant,
            ideation_run=concept_run,
            name="Harvest Nights",
            subtitle="Cozy autumn specials",
            reasoning="Celebrates seasonal produce",
            tags=["Seasonal", "Local"],
            rank_order=1,
        )

        dish_run = models.IdeationRun.objects.create(
            restaurant=self.restaurant,
            initiated_by_user=self.user,
            type=models.IdeationRun.RunType.DISHES,
            model_name="model",
            temperature=0,
            classic_creative=50,
            context_snapshot={},
            parent_concept=self.concept,
            status=models.IdeationRun.Status.SUCCEEDED,
        )
        self.dish = models.DishIdea.objects.create(
            restaurant=self.restaurant,
            ideation_run=dish_run,
            parent_concept=self.concept,
            title="Sweet Corn Bisque",
            description="Silky corn soup",
            ingredient_names=["Sweet corn", "Cream"],
            category_tags=["Seasonal", "Comfort"],
        )

        self.client.login(username="searcher@example.com", password="pw12345")

    def test_tag_search_returns_concepts_and_dishes(self):
        response = self.client.get(reverse("tag-search"), {"tag": "seasonal"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.concept.name)
        self.assertContains(response, self.dish.title)
        self.assertContains(response, self.dish.parent_concept.name)

    def test_tag_search_excludes_other_accounts(self):
        other_account = models.Account.objects.create(name="Other")
        other_restaurant = models.Restaurant.objects.create(
            account=other_account,
            name="Elsewhere",
            location_text="Town",
        )
        other_user = User.objects.create_user("other@example.com", password="pw")
        other_run = models.IdeationRun.objects.create(
            restaurant=other_restaurant,
            initiated_by_user=other_user,
            type=models.IdeationRun.RunType.CONCEPTS,
            model_name="model",
            temperature=0,
            classic_creative=50,
            context_snapshot={},
            status=models.IdeationRun.Status.SUCCEEDED,
        )
        models.Concept.objects.create(
            restaurant=other_restaurant,
            ideation_run=other_run,
            name="Spicy Night",
            subtitle="Bold flavors",
            reasoning="Focus on heat",
            tags=["Seasonal"],
            rank_order=1,
        )

        response = self.client.get(reverse("tag-search"), {"tag": "seasonal"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.concept.name)
        self.assertNotContains(response, "Spicy Night")

    def test_tag_search_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("tag-search"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])
