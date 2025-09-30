"""Views for the internal health dashboard."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Iterable

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db.models import Count, Q
from django.db.models.functions import TruncWeek
from django.http import HttpResponseForbidden
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import TemplateView

from app import models as app_models

User = get_user_model()


@dataclass(frozen=True)
class QuickAction:
    """Represents a lightweight shortcut rendered in the dashboard."""

    label: str
    url: str
    description: str
    icon: str


class DashboardView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    """Render a read-only overview of product health."""

    template_name = "dashboard/overview.html"
    login_url = reverse_lazy("login")
    STALLED_DAYS = 3

    def test_func(self) -> bool:  # pragma: no cover - trivial wrapper
        """Restrict access to staff members only."""

        return bool(self.request.user and self.request.user.is_staff)

    def handle_no_permission(self):  # pragma: no cover - simple guard
        """Return a 403 for authenticated users and fallback to login otherwise."""

        if self.request.user.is_authenticated:
            return HttpResponseForbidden()
        return super().handle_no_permission()

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Assemble the dashboard context broken down by feature areas."""

        context = super().get_context_data(**kwargs)
        context.update(
            {
                "onboarding": self._build_onboarding_context(),
                "subscriptions": self._build_subscription_context(),
                "operations": self._build_operations_context(),
                "engagement": self._build_engagement_context(),
                "business": self._build_business_metrics(),
                "extras": self._build_extras_context(),
                "generated_at": timezone.now(),
                "quick_actions": self._build_quick_actions(),
            }
        )
        return context

    def _build_onboarding_context(self) -> dict[str, Any]:
        """Return counts and stalled records for onboarding progress."""

        now = timezone.now()
        seven_days = now - timedelta(days=7)
        thirty_days = now - timedelta(days=30)
        stalled_cutoff = now - timedelta(days=self.STALLED_DAYS)

        onboardings = (
            app_models.Onboarding.objects.select_related("user")
            .order_by("-created_at")
            .all()
        )
        records: list[dict[str, Any]] = []
        for onboarding in onboardings[:25]:
            updated_at = onboarding.updated_at or onboarding.created_at
            is_complete = onboarding.state == app_models.Onboarding.State.COMPLETE
            stalled = (updated_at or onboarding.created_at) < stalled_cutoff and not is_complete
            records.append(
                {
                    "email": onboarding.user.email if onboarding.user_id else "-",
                    "state": onboarding.get_state_display(),
                    "created_at": onboarding.created_at,
                    "updated_at": updated_at,
                    "stalled": stalled,
                    "restaurant": getattr(onboarding.restaurant, "name", ""),
                }
            )

        return {
            "signups_7": User.objects.filter(date_joined__gte=seven_days).count(),
            "signups_30": User.objects.filter(date_joined__gte=thirty_days).count(),
            "stalled_days": self.STALLED_DAYS,
            "records": records,
        }

    def _build_subscription_context(self) -> dict[str, Any]:
        """Return active subscription counts and churn insights."""

        active_statuses: Iterable[str] = (
            app_models.Subscription.Status.TRIALING,
            app_models.Subscription.Status.ACTIVE,
            app_models.Subscription.Status.PAST_DUE,
        )
        active_by_plan = (
            app_models.Subscription.objects.filter(status__in=active_statuses)
            .values("plan__code", "plan__name")
            .annotate(total=Count("id"))
            .order_by("plan__name")
        )

        trial_days = getattr(settings, "STRIPE_TRIAL_DAYS", 14)
        canceled_candidates = (
            app_models.Subscription.objects.filter(
                status=app_models.Subscription.Status.CANCELED
            )
            .select_related("plan", "account")
            .order_by("-updated_at")[:15]
        )
        canceled_before_trial: list[dict[str, Any]] = []
        for subscription in canceled_candidates:
            period = subscription.current_period_end - subscription.created_at
            if period.days <= trial_days:
                canceled_before_trial.append(
                    {
                        "account": subscription.account.name or str(subscription.account_id),
                        "plan": subscription.plan.name,
                        "canceled_on": subscription.updated_at,
                        "period_days": max(period.days, 0),
                    }
                )

        return {
            "active_by_plan": list(active_by_plan),
            "canceled_before_trial": canceled_before_trial,
            "trial_days": trial_days,
        }

    def _build_operations_context(self) -> dict[str, Any]:
        """Return operational health signals (errors, webhooks, jobs)."""

        failure_counts = (
            app_models.Job.objects.filter(status=app_models.Job.Status.FAILED)
            .values("kind")
            .annotate(total=Count("id"))
            .order_by("-total")
        )
        job_failures = [
            {
                "label": kind_row["kind"].replace("_", " ").title(),
                "total": kind_row["total"],
            }
            for kind_row in failure_counts
        ]

        recent_errors = (
            app_models.Job.objects.filter(status=app_models.Job.Status.FAILED)
            .select_related("restaurant")
            .order_by("-created_at")[:8]
        )
        error_feed = [
            {
                "kind": error.get_kind_display(),
                "restaurant": getattr(error.restaurant, "name", ""),
                "message": error.error_message or "",
                "created_at": error.created_at,
            }
            for error in recent_errors
        ]

        payloads = (
            app_models.OutscraperPayload.objects.filter(
                status=app_models.OutscraperPayload.Status.FAILED
            )
            .select_related("restaurant")
            .order_by("-created_at")[:6]
        )
        webhook_failures = []
        for payload in payloads:
            retry_total = app_models.OutscraperPayload.objects.filter(
                restaurant=payload.restaurant
            ).count()
            webhook_failures.append(
                {
                    "restaurant": getattr(payload.restaurant, "name", ""),
                    "created_at": payload.created_at,
                    "retry_count": max(retry_total - 1, 0),
                    "message": payload.error_message or "",
                }
            )

        pending_jobs = app_models.Job.objects.filter(
            status__in=[
                app_models.Job.Status.QUEUED,
                app_models.Job.Status.RUNNING,
            ]
        ).count()
        failed_jobs = app_models.Job.objects.filter(
            status=app_models.Job.Status.FAILED
        ).count()

        return {
            "job_failures": job_failures,
            "error_feed": error_feed,
            "webhook_failures": webhook_failures,
            "queue_health": {
                "pending": pending_jobs,
                "failed": failed_jobs,
            },
        }

    def _build_engagement_context(self) -> dict[str, Any]:
        """Return engagement metrics and recent user activity."""

        specials_model = self._safe_get_model("app", "Special")
        menu_model = app_models.MenuCollection

        specials_feed: list[dict[str, Any]] = []
        if specials_model is not None:
            for special in specials_model.objects.order_by("-created_at")[:5]:
                specials_feed.append(
                    {
                        "title": getattr(special, "title", "Special"),
                        "created_at": getattr(special, "created_at", None),
                        "status": "Published" if getattr(special, "published", False) else "Draft",
                    }
                )

        menus_feed = [
            {
                "name": menu.name,
                "created_at": menu.created_at,
                "restaurant": getattr(menu.restaurant, "name", ""),
            }
            for menu in menu_model.objects.select_related("restaurant").order_by("-created_at")[:5]
        ]

        inactivity_threshold = timezone.now() - timedelta(days=30)
        active_account_ids = app_models.UiEvent.objects.filter(
            created_at__gte=inactivity_threshold
        ).values_list("restaurant__account_id", flat=True)
        inactive_accounts = (
            app_models.Account.objects.exclude(id__in=active_account_ids)
            .order_by("name")[:10]
        )

        email_stats_raw = (
            app_models.Notification.objects.filter(
                channel=app_models.Notification.Channel.EMAIL
            )
            .values("type")
            .annotate(
                sent=Count("id"),
                opened=Count("id", filter=Q(status=app_models.Notification.Status.READ)),
            )
        )
        email_stats = []
        for item in email_stats_raw:
            sent = item["sent"] or 0
            opened = item["opened"] or 0
            open_rate = (opened / sent * 100) if sent else 0
            label = item["type"].replace("_", " ").title()
            email_stats.append(
                {
                    "label": label,
                    "sent": sent,
                    "opened": opened,
                    "open_rate": round(open_rate, 1),
                }
            )

        return {
            "specials": specials_feed,
            "menus": menus_feed,
            "inactive_accounts": [
                {
                    "name": account.name or str(account.id),
                    "created_at": account.created_at,
                }
                for account in inactive_accounts
            ],
            "email_stats": email_stats,
        }

    def _build_business_metrics(self) -> dict[str, Any]:
        """Return conversion funnel and growth trend data."""

        now = timezone.now()
        recent_weeks = now - timedelta(weeks=12)
        weekly_signups = (
            User.objects.filter(date_joined__gte=recent_weeks)
            .annotate(week=TruncWeek("date_joined"))
            .values("week")
            .annotate(total=Count("id"))
            .order_by("week")
        )
        growth_points = [
            {
                "week": entry["week"],
                "total": entry["total"],
            }
            for entry in weekly_signups
        ]

        specials_model = self._safe_get_model("app", "Special")
        published_specials = 0
        if specials_model is not None:
            published_specials = specials_model.objects.filter(published=True).count()

        return {
            "funnel": {
                "signups": User.objects.count(),
                "restaurants": app_models.Restaurant.objects.count(),
                "published_specials": published_specials,
            },
            "growth": growth_points,
        }

    def _build_extras_context(self) -> dict[str, Any]:
        """Return additional signals such as AI review queues."""

        flagged_outputs = (
            app_models.IdeationRun.objects.filter(status=app_models.IdeationRun.Status.FAILED)
            .select_related("restaurant")
            .order_by("-created_at")[:5]
        )
        flagged = [
            {
                "restaurant": getattr(run.restaurant, "name", ""),
                "created_at": run.created_at,
                "message": run.error_message or "",
            }
            for run in flagged_outputs
        ]

        support_model = self._safe_get_model("appertivo.leads", "Lead")
        support_messages = []
        if support_model is not None:
            for lead in support_model.objects.filter(followed_up=False).order_by("-created_at")[:5]:
                support_messages.append(
                    {
                        "name": lead.name,
                        "email": lead.email,
                        "created_at": lead.created_at,
                        "city": lead.city,
                    }
                )

        return {
            "flagged_outputs": flagged,
            "support_messages": support_messages,
        }

    def _build_quick_actions(self) -> list[QuickAction]:
        """Return lightweight links for common follow-up workflows."""

        restaurant_id = (
            app_models.Restaurant.objects.order_by("-created_at")
            .values_list("id", flat=True)
            .first()
        )
        refresh_url = (
            str(
                reverse_lazy(
                    "refresh_reviews",
                    kwargs={"restaurant_id": restaurant_id},
                )
            )
            if restaurant_id
            else "#"
        )

        return [
            QuickAction(
                label="Article Studio",
                url=str(reverse_lazy("articles:staff_dashboard")),
                description="Launch the staff-only article ideation and publishing flow.",
                icon="newspaper",
            ),
            QuickAction(
                label="Resend onboarding email",
                url=str(reverse_lazy("onboarding-retry")),
                description="Send a fresh activation email to nudge new users.",
                icon="envelope",
            ),
            QuickAction(
                label="Refresh reviews",
                url=refresh_url,
                description="Kick off a new Outscraper pull for the latest reviews.",
                icon="arrows-rotate",
            ),
            QuickAction(
                label="Impersonate account",
                url="/admin/login/?next=/admin/",
                description="Jump into the admin to impersonate or assist.",
                icon="user-secret",
            ),
        ]

    def _safe_get_model(self, app_label: str, model_name: str):
        """Return a model when present without failing the dashboard."""

        try:
            return apps.get_model(app_label, model_name)
        except LookupError:
            return None
