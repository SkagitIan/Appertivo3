from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0010_llmcalllog"),
    ]

    operations = [
        migrations.AddField(
            model_name="concept",
            name="is_unfavorite",
            field=models.BooleanField(default=False),
        ),
    ]
