import json

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.urls import reverse

from app.models import Account, Membership, Onboarding, Restaurant
from swipe.models import Concept, Dish
from swipe.views import SwipeHomeView


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
        Membership.objects.create(account=self.account, user=self.user)
        Onboarding.objects.create(
            user=self.user,
            restaurant=self.restaurant,
            state=Onboarding.State.COMPLETE,
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
        self.factory = RequestFactory()

    def _home_context(self):
        request = self.factory.get(reverse("swipe:home"))
        request.user = self.user
        response = SwipeHomeView.as_view()(request)
        response.render()
        return response.context_data

    def test_context_reflects_seen_state(self):
        context = self._home_context()

        concepts = context["concepts"]
        self.assertEqual(len(concepts), 1)

        concept = concepts[0]
        self.assertFalse(concept.is_seen)
        dish = list(concept.dishes.all())[0]
        self.assertFalse(dish.is_seen)

        Concept.objects.filter(id=self.concept.id).update(is_seen=True)
        Dish.objects.filter(id=self.dish.id).update(is_seen=True)

        context = self._home_context()
        concept = context["concepts"][0]
        self.assertTrue(concept.is_seen)
        dish = list(concept.dishes.all())[0]
        self.assertTrue(dish.is_seen)

    def test_mark_seen_api_creates_records(self):
        self.client.force_login(self.user)
        url = reverse("swipe:mark_seen")

        response = self.client.post(
            url,
            data=json.dumps({"type": "concept", "id": self.concept.id}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"")
        self.concept.refresh_from_db()
        self.assertTrue(self.concept.is_seen)

        response = self.client.post(
            url,
            data=json.dumps({"type": "dish", "id": self.dish.id}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"")
        self.dish.refresh_from_db()
        self.assertTrue(self.dish.is_seen)
