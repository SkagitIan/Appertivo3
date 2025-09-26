"""App configuration for the leads app."""
from django.apps import AppConfig


class LeadsConfig(AppConfig):
    """Configuration for the self-contained leads app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "appertivo.leads"
    verbose_name = "Leads"

    def ready(self) -> None:
        """Import signal handlers when the app is ready."""

        # Import here to avoid triggering side effects before app registry is ready.
        from . import signals  # noqa: F401
