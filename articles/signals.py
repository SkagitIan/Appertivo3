"""Signal handlers for the articles app."""

from __future__ import annotations

import logging

from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

from app.llm import DEFAULT_ARTICLE_OG_IMAGE_URL, generate_article_og_image

from .models import Article

logger = logging.getLogger(__name__)


def _build_og_prompt(article: Article) -> str:
    summary = article.summary or article.seo_description or ""
    summary_fragment = summary[:400]
    prompt = (
        "Editorial food photography for an article aimed at independent restaurant operators. "
        "No text overlay, cinematic lighting, atmospheric background. "
        f"Article title: {article.title}. "
    )
    if summary_fragment:
        prompt += f"Key idea: {summary_fragment}"
    return prompt


@receiver(post_save, sender=Article)
def generate_article_og_image_on_publish(
    sender,
    instance: Article,
    created: bool,
    update_fields,
    **kwargs,
) -> None:
    should_generate = getattr(instance, "_generate_og_on_save", False)
    if not should_generate:
        return

    default_url = getattr(settings, "DEFAULT_ARTICLE_OG_IMAGE_URL", DEFAULT_ARTICLE_OG_IMAGE_URL)
    prompt = _build_og_prompt(instance)

    try:
        image_url = generate_article_og_image(prompt, default_url)
    except Exception:  # pragma: no cover - defensive guard for external failures
        logger.exception("Article %s OG image generation failed", instance.pk)
        return

    if not image_url:
        return

    Article.objects.filter(pk=instance.pk).update(og_image_url=image_url)
    instance._generate_og_on_save = False
