"""Lightweight stand-in for django-htmx during tests."""

from .apps import DjangoHtmxConfig

default_app_config = "django_htmx.apps.DjangoHtmxConfig"

__all__ = ["DjangoHtmxConfig"]
