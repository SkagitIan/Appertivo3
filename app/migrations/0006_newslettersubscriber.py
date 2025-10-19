import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0005_remove_restaurant_menu_urls_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="NewsletterSubscriber",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("email", models.EmailField(max_length=254, unique=True)),
                (
                    "source",
                    models.CharField(
                        blank=True,
                        help_text="Optional tag for where the signup originated.",
                        max_length=64,
                    ),
                ),
            ],
            options={
                "verbose_name": "Newsletter subscriber",
                "verbose_name_plural": "Newsletter subscribers",
            },
        ),
        migrations.AddIndex(
            model_name="newslettersubscriber",
            index=models.Index(fields=["created_at"], name="app_newslet_created__a642be_idx"),
        ),
    ]

