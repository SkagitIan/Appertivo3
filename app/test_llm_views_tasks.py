from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from app import models, tasks, llm


class LLMGenerationTests(TestCase):
    """Tests for mock LLM generation helpers."""

    def test_generate_concepts_returns_nine(self):
        concepts = llm.generate_concepts()
        self.assertEqual(len(concepts), 9)
        self.assertEqual(concepts[0], "Concept 1")


@override_settings(SECURE_SSL_REDIRECT=False)
class ConceptGridViewTests(TestCase):
    """Ensure concept grid renders nine cards."""

    def setUp(self):
        account = models.Account.objects.create(name="Acc")
        restaurant = models.Restaurant.objects.create(
            account=account, name="R", location_text="City"
        )
        run = models.IdeationRun.objects.create(
            restaurant=restaurant,
            initiated_by_user=None,
            type=models.IdeationRun.RunType.CONCEPTS,
            model_name="m",
            temperature=0,
            classic_creative=50,
            context_snapshot={},
            status=models.IdeationRun.Status.SUCCEEDED,
        )
        for i in range(9):
            models.Concept.objects.create(
                restaurant=restaurant, ideation_run=run, name=f"Concept {i}", rank_order=i
            )

    def test_concept_grid_renders_nine_cards(self):
        response = self.client.get(reverse("concepts"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "concept-card", count=9)

@override_settings(SECURE_SSL_REDIRECT=False)
class TaskExecutionTests(TestCase):
    """Tests for external API tasks."""
    def setUp(self):
        self.user = User.objects.create_user("u@example.com")
        self.account = models.Account.objects.create(name="Acc")
        self.restaurant = models.Restaurant.objects.create(
            account=self.account, name="R", location_text="City"
        )

    @patch("app.tasks.requests.get")
    def test_run_outscraper_search_updates_payload(self, mock_get):
        mock_get.return_value.json.return_value = {
            "data": [],
            "menu_link": "http://example.com/menu",
        }
        mock_get.return_value.status_code = 200
        payload = models.OutscraperPayload.objects.create(
            restaurant=self.restaurant,
            status=models.OutscraperPayload.Status.QUEUED,
            request_params={"q": "pizza"},
        )
        tasks.run_outscraper_search(payload.id)
        payload.refresh_from_db()
        self.assertEqual(
            payload.status, models.OutscraperPayload.Status.SUCCEEDED
        )
        self.assertEqual(payload.response_json["data"], [])
        self.assertEqual(payload.discovered_menu_url, "http://example.com/menu")

    @patch("app.tasks.requests.get")
    def test_scrape_menu_updates_menu_version(self, mock_get):
        mock_get.return_value.text = "menu markdown"
        mv = models.MenuVersion.objects.create(
            restaurant=self.restaurant,
            source_url="http://example.com/menu",
            source_kind=models.MenuVersion.SourceKind.URL_SCRAPE,
            raw_markdown="",
            status=models.MenuVersion.Status.QUEUED,
        )
        tasks.scrape_menu(mv.id)
        mv.refresh_from_db()
        self.assertEqual(mv.status, models.MenuVersion.Status.SUCCEEDED)
        self.assertEqual(mv.raw_markdown, "menu markdown")
