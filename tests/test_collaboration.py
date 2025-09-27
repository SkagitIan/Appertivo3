import datetime
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from app import models


class CollaborationFlowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("chef@example.com", password="pw")
        self.account = models.Account.objects.create(name="Account")
        models.Membership.objects.create(account=self.account, user=self.user)
        self.restaurant = models.Restaurant.objects.create(
            account=self.account,
            name="Test Kitchen",
            location_text="City",
        )
        self.menu = models.MenuCollection.objects.create(
            restaurant=self.restaurant,
            created_by_user=self.user,
            name="Dinner",
        )
        run = models.IdeationRun.objects.create(
            restaurant=self.restaurant,
            initiated_by_user=self.user,
            type=models.IdeationRun.RunType.DISHES,
            model_name="test",
            temperature=0,
            classic_creative=50,
            context_snapshot={},
            status=models.IdeationRun.Status.SUCCEEDED,
        )
        concept = models.Concept.objects.create(
            restaurant=self.restaurant,
            ideation_run=run,
            name="Concept",
            subtitle="",
            reasoning="",
            tags=[],
            rank_order=0,
        )
        self.dish = models.DishIdea.objects.create(
            restaurant=self.restaurant,
            ideation_run=run,
            parent_concept=concept,
            title="Herb Crusted Salmon",
            description="With lemon",
            ingredient_names=[],
            category_tags=[],
        )
        models.MenuItem.objects.create(
            menu=self.menu,
            dish=self.dish,
            position=1,
        )
        self.client.login(username="chef@example.com", password="pw")

    def test_enable_collaboration_creates_link(self):
        url = reverse("menu-collaboration-manage", args=[self.menu.id])
        response = self.client.post(url, {"action": "enable", "expires_in_days": "7"})
        self.assertEqual(response.status_code, 302)
        link = models.CollaborationLink.objects.get(menu=self.menu)
        delta = link.expires_at - timezone.now()
        self.assertGreater(delta, datetime.timedelta(days=6))
        self.assertTrue(link.is_active)

    def test_collaboration_dashboard_requires_passcode(self):
        link = models.CollaborationLink.objects.create(
            menu=self.menu,
            expires_at=timezone.now() + datetime.timedelta(days=7),
            passcode="secret",
        )
        response = self.client.get(reverse("collaboration-dashboard", args=[link.id]))
        self.assertContains(response, "Enter passcode")
        post = self.client.post(
            reverse("collaboration-dashboard", args=[link.id]),
            {"passcode": "secret"},
        )
        self.assertEqual(post.status_code, 302)
        follow = self.client.get(post["Location"])
        self.assertContains(follow, "Menu dishes")
        session_key = f"collab_access_{link.id}"
        self.assertTrue(self.client.session.get(session_key))

    def test_staff_feedback_submission_creates_records(self):
        link = models.CollaborationLink.objects.create(
            menu=self.menu,
            expires_at=timezone.now() + datetime.timedelta(days=7),
        )
        dashboard = self.client.get(reverse("collaboration-dashboard", args=[link.id]))
        self.assertEqual(dashboard.status_code, 200)
        payload = {
            "type": models.Feedback.Type.COMMENT,
            "dish_id": str(self.dish.id),
            "comment": "Love the citrus finish",
            "anon_id": "anon1234",
        }
        submit = self.client.post(
            reverse("collaboration-feedback", args=[link.id]),
            payload,
        )
        self.assertEqual(submit.status_code, 302)
        feedback = models.Feedback.objects.get(link=link)
        self.assertEqual(feedback.payload["comment"], "Love the citrus finish")
        self.assertEqual(feedback.action.status, models.FeedbackAction.Status.PENDING)

    def test_chef_can_approve_feedback(self):
        link = models.CollaborationLink.objects.create(
            menu=self.menu,
            expires_at=timezone.now() + datetime.timedelta(days=7),
        )
        feedback = models.Feedback.objects.create(
            menu=self.menu,
            dish=self.dish,
            link=link,
            type=models.Feedback.Type.THUMBS_UP,
            payload={},
            anon_id="anon",
        )
        action = models.FeedbackAction.objects.create(feedback=feedback)
        url = reverse("menu-feedback-action", args=[feedback.id])
        resp = self.client.post(url, {"status": models.FeedbackAction.Status.APPROVED})
        self.assertEqual(resp.status_code, 302)
        action.refresh_from_db()
        self.assertEqual(action.status, models.FeedbackAction.Status.APPROVED)
        self.assertEqual(action.decided_by, self.user)
