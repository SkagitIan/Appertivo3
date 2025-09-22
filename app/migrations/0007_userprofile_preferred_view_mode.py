from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0006_concept_reasoning_concept_tags"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="preferred_view_mode",
            field=models.TextField(default="gallery"),
        ),
    ]
