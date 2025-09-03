"""Celery tasks for content generation."""
from celery import shared_task


@shared_task
def deep_research_task(brief: str) -> dict:
    """Simulate an expensive research step."""
    return {"sources": [f"Research based on {brief}"]}
