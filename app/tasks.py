"""Celery task definitions for external service calls."""

import requests, os
from celery import shared_task
from django.conf import settings
from django.core.files.storage import default_storage
from django.utils import timezone
from app import llm, models
from dotenv import load_dotenv
load_dotenv()
import logging
logger = logging.getLogger(__name__)
from django.db import transaction
from . import models
from openai import OpenAI
_openai_api_key = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=_openai_api_key) if _openai_api_key else None
from . import tasks
from . import pipeline_runner
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
# tasks.py

@shared_task(bind=True, retry_backoff=True)
def send_activation_email(self, email, restaurant_name, activation_link):
    html = render_to_string(
        "emails/activation_email.html",
        {
            "user_email": email,
            "restaurant_name": restaurant_name,
            "activation_link": activation_link,
        },
    )
    msg = EmailMultiAlternatives(
        subject="Activate Your Appertivo Account",
        body=f"Activate here: {activation_link}",
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[email],
    )
    msg.attach_alternative(html, "text/html")
    msg.send()


@shared_task
def run_onboarding_pipeline(onboarding_id):
    pipeline = pipeline_runner.OnboardingPipeline(onboarding_id)
    pipeline.run_all()
    
@shared_task
def create_ideation_run(restaurant_id, user_id, context):
    IdeationRun = apps.get_model("yourappname", "IdeationRun")
    Restaurant = apps.get_model("yourappname", "Restaurant")
    User = apps.get_model("auth", "User")

    restaurant = Restaurant.objects.get(id=restaurant_id)
    user = User.objects.get(id=user_id)

    run = IdeationRun.objects.create(
        restaurant=restaurant,
        initiated_by_user=user,
        type=IdeationRun.RunType.CONCEPTS,
        model_name="gpt-4.1-mini",
        temperature=0.5,
        classic_creative=50,
        context_snapshot={"context": context},
        status=IdeationRun.Status.SUCCEEDED,
    )
    return run.id


@shared_task
def generate_concepts_task() -> list:
    """Wrapper task around the mock LLM concept generator."""
    return llm.generate_concepts()


@shared_task
def generate_dishes_task(concept: str) -> list:
    """Wrapper task around the mock LLM dish generator."""
    return llm.generate_dishes(concept)


@shared_task
def enhance_dish_task(dish_id: str) -> dict:
    """Trigger dish enhancement via background worker."""
    try:
        dish = (
            models.DishIdea.objects.select_related("restaurant")
            .filter(is_deleted=False)
            .get(id=dish_id)
        )
    except models.DishIdea.DoesNotExist:  # pragma: no cover - defensive
        logger.warning("Dish %s not found for enhancement", dish_id)
        return {}

    return llm.enhance_dish(dish, dish.restaurant)


@shared_task
def parse_pdf_menu(menu_version_id: str, storage_path: str):
    """Send PDF to OpenAI to parse into Markdown."""
    mv = models.MenuVersion.objects.get(id=menu_version_id)
    mv.status = models.MenuVersion.Status.RUNNING
    mv.save(update_fields=["status"])

    try:
        with default_storage.open(storage_path, "rb") as f:
            files = {"file": (os.path.basename(storage_path), f, "application/pdf")}
            headers = {"Authorization": f"Bearer {settings.OPENAI_API_KEY}"}

            response = requests.post(
                "https://api.openai.com/v1/files",
                headers=headers,
                files=files,
            )
            file_id = response.json()["id"]

        payload = {
            "model": "gpt-4.1-mini",
            "input": [
                {
                    "role": "system",
                    "content": "You are an expert at extracting restaurant menus into clean Markdown.",
                },
                {
                    "role": "user",
                    "content": f"Extract the full menu in Markdown from file {file_id}.",
                },
            ],
        }
        resp = requests.post("https://api.openai.com/v1/responses", json=payload, headers=headers)
        markdown_text = resp.json().get("output_text", "")

        mv.raw_markdown = markdown_text
        mv.status = models.MenuVersion.Status.SUCCEEDED
        mv.parsed_at = timezone.now()
        mv.save(update_fields=["raw_markdown", "status", "parsed_at"])

        restaurant = mv.restaurant
        restaurant.active_menu_version = mv
        restaurant.save(update_fields=["active_menu_version"])

    except Exception as e:
        mv.status = models.MenuVersion.Status.FAILED
        mv.error_message = str(e)
        mv.save(update_fields=["status", "error_message"])

    return mv.raw_markdown
