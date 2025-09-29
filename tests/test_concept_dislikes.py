import json
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from app import models


@override_settings(SECURE_SSL_REDIRECT=False)
class ConceptDislikeTests(TestCase):
    """Tests for tracking and surfacing concepts users removed from favorites."""

    def setUp(self):
        self.user = User.objects.create_user("chef@example.com", password="pw")
        self.account = models.Account.objects.create(name="Acc")
        models.Membership.objects.create(account=self.account, user=self.user)
        self.restaurant = models.Restaurant.objects.create(
            account=self.account,
            name="R",
            location_text="City",
        )
        models.RestaurantSettings.objects.create(restaurant=self.restaurant)
        self.client.login(username="chef@example.com", password="pw")

    def _create_concepts(self, count: int = 3):
        run = models.IdeationRun.objects.create(
            restaurant=self.restaurant,
            initiated_by_user=self.user,
            type=models.IdeationRun.RunType.CONCEPTS,
            model_name="test",
            temperature=0,
            classic_creative=50,
            context_snapshot={},
            status=models.IdeationRun.Status.SUCCEEDED,
        )
        concepts = []
        for idx in range(count):
            concepts.append(
                models.Concept.objects.create(
                    restaurant=self.restaurant,
                    ideation_run=run,
                    name=f"C{idx}",
                    rank_order=idx,
                )
            )
        return concepts

    def _favorite_and_unfavorite(self, concept: models.Concept) -> None:
        self.client.post(
            reverse("concept-favorite", args=[concept.id]), HTTP_HX_REQUEST="true"
        )
        self.client.post(
            reverse("concept-favorite", args=[concept.id]), HTTP_HX_REQUEST="true"
        )

    def test_unfavorite_records_concept_name_in_session(self):
        concept = self._create_concepts(1)[0]
        self._favorite_and_unfavorite(concept)
        self.assertIn(concept.name, self.client.session.get("disliked_concepts", []))

    def test_settings_page_displays_disliked_section(self):
        concept = self._create_concepts(1)[0]
        self._favorite_and_unfavorite(concept)

        response = self.client.get(reverse("settings"))
        self.assertContains(response, "Passed on concepts")
        self.assertContains(response, "👎")
        self.assertContains(response, concept.name)

    @patch("app.views.client")
    def test_generation_context_mentions_disliked_concepts(self, mock_client):
        concept = self._create_concepts(1)[0]
        self._favorite_and_unfavorite(concept)

        payload = {
            "concepts": [
                {
                    "title": f"New {idx}",
                    "subtitle": "",
                    "reasoning": "",
                    "tags": ["tag"],
                }
                for idx in range(9)
            ]
        }
        mock_client.responses.create.return_value = SimpleNamespace(
            output=[
                SimpleNamespace(
                    content=[SimpleNamespace(text=json.dumps(payload))]
                )
            ]
        )

        response = self.client.post(
            reverse("concepts-generate"),
            {"prompt": ""},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)

        _, kwargs = mock_client.responses.create.call_args
        context_text = kwargs["input"][1]["content"]
        self.assertIn(concept.name, context_text)
        self.assertIn("passed on", context_text.lower())

