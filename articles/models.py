from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify


class PromptTemplate(models.Model):
    STEP_CHOICES = [
        ("ideas", "Idea Generation"),
        ("scoring", "Scoring & Selection"),
        ("outline", "Outline + Sources"),
        ("draft", "Draft Article"),
        ("polish", "Professional Rewrite"),
        ("seo", "SEO Optimization"),
    ]
    name = models.CharField(max_length=50, choices=STEP_CHOICES, unique=True)
    prompt_text = models.TextField()
    last_updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:  # pragma: no cover - trivial representation
        return dict(self.STEP_CHOICES).get(self.name, self.name)


class ArticleRun(models.Model):
    created_by = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(
        max_length=20,
        choices=[
            ("queued", "Queued"),
            ("running", "Running"),
            ("failed", "Failed"),
            ("completed", "Completed"),
            ("canceled", "Canceled"),
        ],
        default="queued",
    )
    current_step = models.CharField(max_length=20, blank=True, null=True)
    error_message = models.TextField(blank=True, null=True)
    can_resume_from_step = models.BooleanField(default=False)
    cost_cents = models.IntegerField(default=0)
    model_info = models.CharField(max_length=100, default="gpt-4.1-nano")

    class Meta:
        ordering = ["-created_at"]

    def mark_failed(self, error_message: str, *, step: Optional["RunStep"] = None) -> None:
        self.status = "failed"
        self.error_message = error_message
        if step:
            self.current_step = step.name
            self.can_resume_from_step = True
        self.save(update_fields=["status", "error_message", "current_step", "can_resume_from_step"])

    def __str__(self) -> str:  # pragma: no cover - trivial representation
        return f"Run {self.pk} ({self.status})"


class RunStep(models.Model):
    run = models.ForeignKey(ArticleRun, on_delete=models.CASCADE, related_name="steps")
    name = models.CharField(max_length=50)
    status = models.CharField(
        max_length=20,
        choices=[
            ("queued", "Queued"),
            ("running", "Running"),
            ("failed", "Failed"),
            ("ok", "Ok"),
        ],
        default="queued",
    )
    input_payload = models.JSONField(default=dict)
    output_payload = models.JSONField(default=dict)
    raw_response = models.JSONField(default=dict)
    started_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(blank=True, null=True)
    error_message = models.TextField(blank=True, null=True)
    retries = models.IntegerField(default=0)

    class Meta:
        ordering = ["started_at"]

    def __str__(self) -> str:  # pragma: no cover - trivial representation
        return f"{self.name} ({self.status})"


class Article(models.Model):
    title = models.CharField(max_length=255)
    slug = models.SlugField(unique=True, max_length=255)
    summary = models.TextField(blank=True)
    outline_json = models.JSONField(default=dict)
    body_markdown = models.TextField()
    sources_json = models.JSONField(default=list)
    seo_title = models.CharField(max_length=70, blank=True)
    seo_description = models.CharField(max_length=160, blank=True)
    og_image_url = models.URLField(blank=True, null=True)
    status = models.CharField(
        max_length=20,
        choices=[("draft", "Draft"), ("published", "Published")],
        default="draft",
    )
    published_at = models.DateTimeField(blank=True, null=True)
    run = models.ForeignKey(ArticleRun, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ["-published_at", "-id"]

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self.slug:
            self.slug = slugify(self.title)[:250]

        previous_status = None
        if self.pk:
            previous_status = (
                type(self)
                .objects.filter(pk=self.pk)
                .values_list("status", flat=True)
                .first()
            )

        status_changed_to_published = False
        if self.status == "published":
            if not self.published_at:
                self.published_at = timezone.now()
            if previous_status != "published":
                status_changed_to_published = True
        elif self.status == "draft" and self.published_at:
            # Drafts should not retain a published timestamp to avoid routing confusion.
            self.published_at = None

        self._generate_og_on_save = status_changed_to_published
        super().save(*args, **kwargs)

    def get_absolute_url(self) -> str:
        if self.published_at:
            return reverse(
                "articles:article_detail",
                kwargs={
                    "year": self.published_at.year,
                    "month": f"{self.published_at:%m}",
                    "slug": self.slug,
                },
            )
        return reverse("articles:article_index")

    def __str__(self) -> str:  # pragma: no cover - trivial representation
        return self.title

    def to_outline(self) -> Dict[str, Any]:
        """Return a safe outline representation for templates."""

        try:
            if isinstance(self.outline_json, str):
                return json.loads(self.outline_json)
        except json.JSONDecodeError:
            return {}
        return self.outline_json or {}

    @property
    def sources(self) -> List[Dict[str, Any]]:
        """Return a normalized list of source dictionaries."""

        if isinstance(self.sources_json, str):
            try:
                return json.loads(self.sources_json)
            except json.JSONDecodeError:
                return []
        return list(self.sources_json or [])


class ArticleIdea(models.Model):
    """A saved idea concept from Step 1 for future reuse."""

    created_by = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    title = models.CharField(max_length=255)
    subtitle = models.TextField(blank=True)
    angle = models.TextField(blank=True)
    source_run = models.ForeignKey(ArticleRun, on_delete=models.SET_NULL, null=True, blank=True)
    archived = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.title
