from django.test import TestCase, override_settings
from django.urls import reverse
from unittest.mock import patch

from app.models import SpecialDraft
from app.tasks import enhance_image_task, retool_description_task


class SpecialDraftModelTests(TestCase):
    def test_defaults(self):
        draft = SpecialDraft.objects.create()
        self.assertTrue(draft.image_ai_enabled)
        self.assertTrue(draft.desc_ai_enabled)
        self.assertEqual(draft.desc_status, "idle")
        self.assertEqual(draft.current_step, 0)
        self.assertEqual(draft.concept, "")


class SpecialDraftTasksTests(TestCase):
    def setUp(self):
        self.draft = SpecialDraft.objects.create(description_user="Fresh tacos", concept="Fiesta")

    def test_enhance_image_task(self):
        enhance_image_task(self.draft.id)
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.image_status, "ready")
        self.assertTrue(self.draft.enhanced_image_url)

    def test_retool_description_task(self):
        retool_description_task(self.draft.id)
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.desc_status, "ready")
        self.assertIn("Fiesta", self.draft.description_ai)


@override_settings(SECURE_SSL_REDIRECT=False)
class SpecialDraftViewsTests(TestCase):
    def test_step_views(self):
        for step in range(0, 5):
            url = reverse("special_draft_step", args=[step])
            with patch("app.special_draft_views.get_concepts_for_today", return_value=["A"]):
                response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertTemplateUsed(response, f"app/special_draft/_step{step}_modal.html")

    def test_invalid_step(self):
        url = reverse("special_draft_step", args=[5])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_step0_concepts(self):
        with patch("app.special_draft_views.get_concepts_for_today", return_value=["Seafood"]):
            url = reverse("special_draft_step", args=[0])
            response = self.client.get(url)
            self.assertContains(response, "Seafood")

    def test_select_idea_sets_defaults(self):
        draft = SpecialDraft.objects.create()
        url = reverse("special_draft_select", args=[draft.id])
        response = self.client.post(url, {"concept": "Seafood", "idea": "Grilled Salmon"})
        draft.refresh_from_db()
        self.assertEqual(draft.concept, "Seafood")
        self.assertEqual(draft.title, "Grilled Salmon")
        self.assertEqual(draft.description_user, "Grilled Salmon")
        self.assertEqual(draft.current_step, 1)
        self.assertEqual(response.status_code, 302)

    def test_ideas_endpoint(self):
        with patch("app.special_draft_views.generate_special_ideas", return_value=["Idea"]):
            response = self.client.get(reverse("special_draft_ideas"), {"concept": "Soup"})
        self.assertJSONEqual(response.content, {"ideas": ["Idea"]})
