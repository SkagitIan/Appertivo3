import json
from types import SimpleNamespace
from unittest.mock import patch

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
        self.user = User.objects.create_user(
            username="viewer@example.com", password="pass1234"
        )
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
        self.client.login(username="viewer@example.com", password="pass1234")

    def test_concept_grid_renders_nine_cards(self):
        response = self.client.get(reverse("concepts"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertGreaterEqual(content.count("concept-card"), 9)
        self.assertEqual(len(response.context["concepts"]), 9)



@override_settings(SECURE_SSL_REDIRECT=False)
class DishVariationViewTests(TestCase):
    """Ensure dish variations can be generated on demand."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="chef@example.com", password="pass1234"
        )
        account = models.Account.objects.create(name="Chef Co")
        models.Membership.objects.create(
            account=account, user=self.user, role=models.Membership.Role.OWNER
        )
        self.restaurant = models.Restaurant.objects.create(
            account=account,
            name="Flavor Town",
            location_text="Somewhere",
            context_json={"name": "Flavor Town"},
        )
        concept_run = models.IdeationRun.objects.create(
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
            ideation_run=concept_run,
            name="Garden Fresh",
            subtitle="",
            rank_order=1,
        )
        dish_run = models.IdeationRun.objects.create(
            restaurant=self.restaurant,
            initiated_by_user=self.user,
            type=models.IdeationRun.RunType.DISHES,
            model_name="mock",
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
            title="Sunrise Salad",
            description="Citrus-dressed greens.",
            ingredient_names=["orange", "mint"],
            category_tags=["salad"],
        )

    @patch("app.views.client", new=None)
    def test_variation_request_creates_child_dish(self, _client=None):
        self.client.login(username="chef@example.com", password="pass1234")
        url = reverse("dish-variation", args=[self.dish.id])
        response = self.client.post(url, HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 200)
        children = models.DishIdea.objects.filter(parent_dish=self.dish)
        self.assertEqual(children.count(), 1)
        new_dish = children.first()
        self.assertIn(str(new_dish.id), response.content.decode())
        self.assertTrue(new_dish.title.startswith(self.dish.title))


@override_settings(SECURE_SSL_REDIRECT=False)
class DishDetailViewLayoutTests(TestCase):
    """The dish detail page should mark favorited dishes for enhanced layout."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="detailer@example.com", password="pass1234"
        )
        account = models.Account.objects.create(name="Detail Co")
        models.Membership.objects.create(
            account=account, user=self.user, role=models.Membership.Role.OWNER
        )
        self.restaurant = models.Restaurant.objects.create(
            account=account,
            name="Detail Bistro",
            location_text="Metro",
            context_json={"name": "Detail Bistro"},
        )
        concept_run = models.IdeationRun.objects.create(
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
            ideation_run=concept_run,
            name="Detail Concept",
            subtitle="",
            rank_order=1,
        )
        dish_run = models.IdeationRun.objects.create(
            restaurant=self.restaurant,
            initiated_by_user=self.user,
            type=models.IdeationRun.RunType.DISHES,
            model_name="mock",
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
            title="Detail Dish",
            description="Savory goodness.",
            ingredient_names=["herb"],
            category_tags=["entree"],
        )

        models.FavoriteDish.objects.create(
            user=self.user, dish=self.dish, favorited_at=timezone.now()
        )

        self.client.login(username="detailer@example.com", password="pass1234")

    def test_favorited_dish_uses_enhanced_layout(self):
        response = self.client.get(reverse("dish_detail", args=[self.concept.id]))
        self.assertEqual(response.status_code, 200)

        html = response.content.decode()
        self.assertIn("dish-image w-full aspect", html)
        self.assertNotIn("flip-scene", html)
        self.assertIn('aria-pressed="true"', html)


@override_settings(SECURE_SSL_REDIRECT=False)
class SessionHistoryTests(TestCase):
    """Ensure session history is attached to LLM prompts."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="owner@example.com", password="safe-pass"
        )
        account = models.Account.objects.create(name="Session Co")
        models.Membership.objects.create(
            account=account, user=self.user, role=models.Membership.Role.OWNER
        )
        self.restaurant = models.Restaurant.objects.create(
            account=account,
            name="Session Bistro",
            location_text="Metro",
            context_json={"name": "Session Bistro"},
        )
        concept_run = models.IdeationRun.objects.create(
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
            ideation_run=concept_run,
            name="Harvest Harmony",
            subtitle="",
            rank_order=1,
        )

    def _fake_response(self, payload):
        return SimpleNamespace(
            output=[SimpleNamespace(content=[SimpleNamespace(text=json.dumps(payload))])]
        )

    @patch("app.views.client")
    def test_concept_generation_records_session_history(self, mock_client):
        concepts_payload = {
            "concepts": [
                {
                    "title": f"Concept Title {i}",
                    "subtitle": "",
                    "reasoning": "",
                    "tags": ["tag"],
                }
                for i in range(1, 10)
            ]
        }
        mock_client.responses.create.return_value = self._fake_response(concepts_payload)

        self.client.login(username="owner@example.com", password="safe-pass")
        session = self.client.session
        session["generated_concepts"] = ["Sunset Soirée"]
        session["generated_dishes"] = ["Twilight Tacos"]
        session.save()

        response = self.client.post(reverse("concepts-generate"))
        self.assertEqual(response.status_code, 200)

        _, kwargs = mock_client.responses.create.call_args
        input_messages = kwargs.get("input", [])
        self.assertGreaterEqual(len(input_messages), 2)
        system_content = input_messages[0]["content"]
        context_content = input_messages[1]["content"]

        self.assertIn("Sunset Soirée", system_content)
        self.assertNotIn("Twilight Tacos", system_content)
        self.assertIn(
            "Previously generated concept names to avoid: Sunset Soirée",
            context_content,
        )
        self.assertNotIn("Previously generated dish names", context_content)

        session = self.client.session
        stored_concepts = session.get("generated_concepts")
        self.assertIn("Sunset Soirée", stored_concepts)
        self.assertIn("Concept Title 1", stored_concepts)

    @patch("app.views.client")
    def test_concept_generation_accepts_user_prompt(self, mock_client):
        payload = {
            "concepts": [
                {
                    "title": f"Prompt Concept {i}",
                    "subtitle": "",
                    "reasoning": "",
                    "tags": ["tag"],
                }
                for i in range(1, 10)
            ]
        }
        mock_client.responses.create.return_value = self._fake_response(payload)

        self.client.login(username="owner@example.com", password="safe-pass")
        response = self.client.post(
            reverse("concepts-generate"), {"prompt": "Vegan brunch spotlight"}
        )
        self.assertEqual(response.status_code, 200)

        _, kwargs = mock_client.responses.create.call_args
        messages = kwargs.get("input", [])
        self.assertGreaterEqual(len(messages), 2)
        system_content = messages[0]["content"]
        context_content = messages[1]["content"]

        self.assertIn("Vegan brunch spotlight", system_content)
        self.assertIn(
            "User special instructions: Vegan brunch spotlight", context_content
        )

    @patch("app.views.client")
    def test_dish_generation_records_session_history(self, mock_client):
        dishes_payload = {
            "dishes": [
                {
                    "title": f"Dish {i}",
                    "description": "Tasty",
                    "ingredient_overlap": [],
                    "category_tags": ["tag"],
                }
                for i in range(1, 10)
            ]
        }
        mock_client.responses.create.return_value = self._fake_response(dishes_payload)

        self.client.login(username="owner@example.com", password="safe-pass")
        session = self.client.session
        session["generated_concepts"] = ["Harvest Harmony"]
        session["generated_dishes"] = ["Sunrise Salad"]
        session.save()

        response = self.client.get(reverse("dishes-generate", args=[self.concept.id]))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "dishes/page.html")

        _, kwargs = mock_client.responses.create.call_args
        input_messages = kwargs.get("input", [])
        instruction_content = input_messages[0]["content"]
        context_json = json.loads(input_messages[1]["content"])

        self.assertIn("Avoid repeating these dish names: Sunrise Salad", instruction_content)
        self.assertNotIn("Avoid duplicating these concept names", instruction_content)
        self.assertEqual(
            context_json.get("session_history"),
            {"dish_names": ["Sunrise Salad"]},
        )
        self.assertNotIn(
            "concept_names", context_json.get("session_history", {})
        )

        session = self.client.session
        stored_dishes = session.get("generated_dishes")
        self.assertIn("Sunrise Salad", stored_dishes)
        self.assertIn("Dish 1", stored_dishes)

    @patch("app.views.client")
    def test_dish_generation_htmx_returns_partial(self, mock_client):
        dishes_payload = {
            "dishes": [
                {
                    "title": f"Dish {i}",
                    "description": "Tasty",
                    "ingredient_overlap": [],
                    "category_tags": ["tag"],
                }
                for i in range(1, 10)
            ]
        }
        mock_client.responses.create.return_value = self._fake_response(dishes_payload)

        self.client.login(username="owner@example.com", password="safe-pass")

        response = self.client.post(
            reverse("dishes-generate", args=[self.concept.id]),
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "dishes/grid.html")


@override_settings(SECURE_SSL_REDIRECT=False)
class TaskExecutionTests(TestCase):
    """Tests for external API tasks."""
    def setUp(self):
        self.user = User.objects.create_user("u@example.com")
        self.account = models.Account.objects.create(name="Acc")
        self.restaurant = models.Restaurant.objects.create(
            account=self.account, name="R", location_text="City"
        )

    @patch("app.tasks.scrape_menu")
    @patch("app.tasks.requests.get")
    def test_run_outscraper_search_updates_payload(self, mock_get, mock_scrape):
        """If Outscraper finds a menu URL we queue a scrape and persist context."""
        mock_get.return_value.json.return_value = {
            "data": [
                [
                    {
                        "name": "Ristorante Uno",
                        "full_address": "City, ST",
                        "menu_link": "http://example.com/menu",
                        "phone": "123",
                        "site": "http://example.com",
                        "place_id": "abc",
                        "description": "Great food",
                        "rating": 4.7,
                        "reviews": 12,
                        "working_hours": {"mon": "9-5"},
                        "about": {"service": "Takeout"},
                    }
                ]
            ]
        }
        mock_get.return_value.status_code = 200
        payload = models.OutscraperPayload.objects.create(
            restaurant=self.restaurant,
            status=models.OutscraperPayload.Status.QUEUED,
            request_params={"q": "pizza"},
        )

        tasks.run_outscraper_search(str(payload.id))

        payload.refresh_from_db()
        self.restaurant.refresh_from_db()
        self.assertEqual(payload.status, models.OutscraperPayload.Status.SUCCEEDED)
        self.assertEqual(payload.discovered_menu_url, "http://example.com/menu")
        self.assertEqual(self.restaurant.primary_menu_url, "http://example.com/menu")
        self.assertEqual(self.restaurant.menu_urls, ["http://example.com/menu"])
        self.assertEqual(models.MenuVersion.objects.count(), 1)
        menu_version = models.MenuVersion.objects.get()
        self.assertEqual(menu_version.status, models.MenuVersion.Status.QUEUED)
        self.assertEqual(menu_version.source_url, "http://example.com/menu")
        mock_scrape.delay.assert_called_once_with(str(menu_version.id))

    @patch("app.tasks.scrape_menu")
    @patch("app.tasks.requests.get")
    def test_run_outscraper_without_menu_link(self, mock_get, mock_scrape):
        """When no menu is found we still store context but do not queue a scrape."""
        mock_get.return_value.json.return_value = {
            "data": [[{"name": "No Menu", "full_address": "City"}]]
        }
        payload = models.OutscraperPayload.objects.create(
            restaurant=self.restaurant,
            status=models.OutscraperPayload.Status.QUEUED,
            request_params={"q": "pizza"},
        )

        tasks.run_outscraper_search(str(payload.id))

        payload.refresh_from_db()
        self.assertEqual(payload.status, models.OutscraperPayload.Status.SUCCEEDED)
        self.assertIsNone(payload.discovered_menu_url)
        self.assertEqual(models.MenuVersion.objects.count(), 0)
        mock_scrape.delay.assert_not_called()

    @patch("app.tasks.validate_menu_text", return_value=True)
    @patch("app.tasks.requests.get")
    def test_scrape_menu_updates_menu_version(self, mock_get, mock_validate):
        mock_get.return_value.text = "menu markdown"
        mv = models.MenuVersion.objects.create(
            restaurant=self.restaurant,
            source_url="http://example.com/menu",
            source_kind=models.MenuVersion.SourceKind.URL_SCRAPE,
            raw_markdown="",
            status=models.MenuVersion.Status.QUEUED,
        )

        tasks.scrape_menu(str(mv.id))

        mv.refresh_from_db()
        self.restaurant.refresh_from_db()
        self.assertEqual(mv.status, models.MenuVersion.Status.SUCCEEDED)
        self.assertEqual(mv.raw_markdown, "menu markdown")
        self.assertIsNotNone(mv.parsed_at)
        self.assertEqual(self.restaurant.active_menu_version, mv)

    @patch("app.tasks.validate_menu_text", return_value=False)
    @patch("app.tasks.requests.get")
    def test_scrape_menu_marks_failed_when_no_menu(self, mock_get, mock_validate):
        mock_get.return_value.text = "Welcome to our homepage"
        mv = models.MenuVersion.objects.create(
            restaurant=self.restaurant,
            source_url="http://example.com/menu",
            source_kind=models.MenuVersion.SourceKind.URL_SCRAPE,
            raw_markdown="",
            status=models.MenuVersion.Status.QUEUED,
        )

        tasks.scrape_menu(str(mv.id))

        mv.refresh_from_db()
        self.restaurant.refresh_from_db()
        self.assertEqual(mv.status, models.MenuVersion.Status.FAILED)
        self.assertEqual(mv.raw_markdown, "")
        self.assertIsNone(mv.parsed_at)
        self.assertEqual(
            mv.error_message,
            "Scraped page did not look like a menu.",
        )
        self.assertEqual(self.restaurant.active_menu_version, mv)
