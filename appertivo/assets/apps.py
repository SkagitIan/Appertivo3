from django.apps import AppConfig


class AssetsConfig(AppConfig):
    """Configuration for the internal assets app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "appertivo.assets"
    verbose_name = "Internal Assets"
