from django.apps import AppConfig


class ArticlesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "articles"
    verbose_name = "Thought Leadership Articles"

    def ready(self):
        # Lazy import to avoid side effects during migrations.
        from . import signals  # noqa: F401
