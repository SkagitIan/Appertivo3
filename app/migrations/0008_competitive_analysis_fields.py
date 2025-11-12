from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0007_restaurant_coordinates"),
    ]

    operations = [
        migrations.AddField(
            model_name="onboarding",
            name="competitive_analysis",
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="restaurant",
            name="competitive_analysis",
            field=models.JSONField(blank=True, null=True),
        ),
    ]
