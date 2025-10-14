"""Minimal AppConfig stub for django_htmx."""

import os

from django.apps import AppConfig


class HtmxConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "django_htmx"
    verbose_name = "django-htmx"
    path = os.path.dirname(__file__)
