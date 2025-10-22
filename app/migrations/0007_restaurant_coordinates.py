from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0006_newslettersubscriber"),
    ]

    operations = [
        migrations.AddField(
            model_name="restaurant",
            name="latitude",
            field=models.DecimalField(blank=True, decimal_places=6, max_digits=9, null=True),
        ),
        migrations.AddField(
            model_name="restaurant",
            name="longitude",
            field=models.DecimalField(blank=True, decimal_places=6, max_digits=9, null=True),
        ),
    ]
