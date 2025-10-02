"""Database models for the internal assets application."""

from __future__ import annotations

from django.conf import settings
from django.db import models


class AssetPreviewJob(models.Model):
    """Track asynchronous preview generation for an asset model."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"

    model = models.ForeignKey(
        "AssetModel",
        on_delete=models.CASCADE,
        related_name="preview_jobs",
    )
    prompt = models.TextField()
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    preview_url = models.URLField(blank=True)
    storage_path = models.CharField(max_length=500, blank=True)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:  # pragma: no cover - human readable representation
        status = self.get_status_display()
        return f"Preview job for {self.model} ({status})"


class AssetModel(models.Model):
    """Represents a Replicate model staff can run."""

    description = models.CharField(max_length=255)
    identifier = models.CharField(
        max_length=255,
        unique=True,
        help_text="Full Replicate model identifier, including version hash.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["description"]

    def __str__(self) -> str:  # pragma: no cover - human readable representation
        return self.description


class PromptTemplate(models.Model):
    """Reusable text snippets for image prompts."""

    title = models.CharField(max_length=120)
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["title"]

    def __str__(self) -> str:  # pragma: no cover - human readable representation
        return self.title


class GeneratedAsset(models.Model):
    """Stores saved assets generated through Replicate."""

    model = models.ForeignKey(
        AssetModel,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assets",
    )
    prompt = models.TextField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="generated_assets",
    )
    preview_url = models.URLField(blank=True)
    image = models.FileField(upload_to="assets/", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def filename(self) -> str:
        """Return a friendly name for the stored asset."""

        if self.image:
            return self.image.name.split("/")[-1]
        return ""
