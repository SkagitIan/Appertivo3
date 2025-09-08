from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("app", "0016_specialmetrics"),
    ]

    operations = [
        migrations.CreateModel(
            name="SpecialDraft",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("raw_image", models.ImageField(upload_to="drafts/", blank=True, null=True)),
                ("enhanced_image_url", models.URLField(blank=True)),
                ("image_status", models.CharField(default="uploaded", max_length=20)),
                ("image_ai_enabled", models.BooleanField(default=True)),
                ("title", models.CharField(max_length=255, blank=True)),
                ("description_user", models.TextField(blank=True)),
                ("description_ai", models.TextField(blank=True)),
                ("desc_status", models.CharField(default="idle", max_length=20)),
                ("desc_ai_enabled", models.BooleanField(default=True)),
                ("start_at", models.DateTimeField(blank=True, null=True)),
                ("end_at", models.DateTimeField(blank=True, null=True)),
                ("recurrence_type", models.CharField(default="once", max_length=10)),
                ("current_step", models.PositiveSmallIntegerField(default=1)),
            ],
        ),
    ]
