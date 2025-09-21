from django.apps import AppConfig


class DjangoHtmxConfig(AppConfig):
    """Minimal app config so Django can load the placeholder package."""

    default_auto_field = "django.db.models.AutoField"
    name = "django_htmx"
