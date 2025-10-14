import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from app.models import Account, Restaurant
from swipe.models import Concept, Dish, SeenItem


class SwipeSeenTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="tester", password="pass1234")
        self.account = Account.objects.create(name="Test Account")
        self.restaurant = Restaurant.objects.create(
            account=self.account,
            name="Test Resto",
            location_text="123 Anywhere",
        )
        self.concept = Concept.objects.create(
            restaurant=self.restaurant,
            name="Garden Light",
            subtitle="Herb focused menu",
            meta_ingredients=["basil", "mint"],
            meta_reasoning="Fresh herbs lead the dishes.",
        )
        self.dish = Dish.objects.create(
            concept=self.concept,
            name="Minted Pea Soup",
            ingredients=["mint", "peas"],
            reasoning="Bright and refreshing.",
            price="$12",
        )

    def test_context_marks_unseen_items_as_new(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("swipe:home"))

        concepts = response.context["concepts"]
        self.assertEqual(len(concepts), 1)

        concept = concepts[0]
        self.assertTrue(concept.is_new)
        dish = list(concept.dishes.all())[0]
        self.assertTrue(dish.is_new)

        SeenItem.objects.create(
            user=self.user,
            item_type=SeenItem.ItemType.CONCEPT,
            item_id=self.concept.id,
        )
        SeenItem.objects.create(
            user=self.user,
            item_type=SeenItem.ItemType.DISH,
            item_id=self.dish.id,
        )

        response = self.client.get(reverse("swipe:home"))
        concept = response.context["concepts"][0]
        self.assertFalse(concept.is_new)
        dish = list(concept.dishes.all())[0]
        self.assertFalse(dish.is_new)

    def test_mark_seen_api_creates_records(self):
        self.client.force_login(self.user)
        url = reverse("swipe:mark_seen")

        response = self.client.post(
            url,
            data=json.dumps({"type": "concept", "id": self.concept.id}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            SeenItem.objects.filter(
                user=self.user,
                item_type=SeenItem.ItemType.CONCEPT,
                item_id=self.concept.id,
            ).exists()
        )

        response = self.client.post(
            url,
            data=json.dumps({"type": "dish", "id": self.dish.id}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            SeenItem.objects.filter(
                user=self.user,
                item_type=SeenItem.ItemType.DISH,
                item_id=self.dish.id,
            ).exists()
        )
