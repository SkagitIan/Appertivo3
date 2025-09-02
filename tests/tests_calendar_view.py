from datetime import datetime, timedelta
from django.test import TestCase
from django.urls import reverse
from django.contrib.auth.models import User
from django.utils import timezone

from app.models import Special, UserProfile
from app.utils import month_cells


class MonthCellsTests(TestCase):
    def test_month_cells_includes_specials(self):
        user = User.objects.create_user(username="owner", password="pw")
        UserProfile.objects.create(user=user, restaurant_name="R")
        start = timezone.make_aware(datetime(2024, 5, 10, 12, 0))
        end = start + timedelta(hours=2)
        special = Special.objects.create(
            user=user,
            title="Taco",
            description="desc",
            price=5,
            start_date=start,
            end_date=end,
            status="active",
            cta_type="web",
            cta_url="",
        )
        weeks = month_cells(2024, 5, Special.objects.filter(user=user))
        self.assertTrue(all(len(week) == 7 for week in weeks))
        self.assertTrue(
            any(
                cell["date"] == start.date() and special in cell["specials"]
                for week in weeks
                for cell in week
            )
        )


class SpecialsListToggleTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="owner", password="pw")
        self.profile = UserProfile.objects.create(user=self.user, restaurant_name="R")
        self.client.login(username="owner", password="pw")

    def test_toggle_updates_default_view(self):
        response = self.client.get(reverse("specials_list") + "?view=calendar")
        self.assertTemplateUsed(response, "app/calendar.html")
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.default_view, "calendar")


class CalendarEventsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="owner", password="pw")
        UserProfile.objects.create(user=self.user, restaurant_name="R")
        self.client.login(username="owner", password="pw")

    def test_calendar_view_includes_status_colors(self):
        start = timezone.now()
        end = start + timedelta(hours=1)
        active = Special.objects.create(
            user=self.user,
            title="Active",
            description="desc",
            price=5,
            start_date=start,
            end_date=end,
            status="active",
            cta_type="web",
            cta_url="",
        )
        expired = Special.objects.create(
            user=self.user,
            title="Expired",
            description="desc",
            price=5,
            start_date=start - timedelta(days=1),
            end_date=end - timedelta(days=1),
            status="expired",
            cta_type="web",
            cta_url="",
        )
        response = self.client.get(reverse("specials_list") + "?view=calendar")
        events = response.context["events"]
        colors = {e["id"]: e["color"] for e in events}
        self.assertEqual(colors[str(active.id)], "#22c55e")
        self.assertEqual(colors[str(expired.id)], "#6b7280")
