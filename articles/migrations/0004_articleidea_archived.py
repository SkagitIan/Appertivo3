from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("articles", "0003_articleidea_library"),
    ]

    operations = [
        migrations.AddField(
            model_name="articleidea",
            name="archived",
            field=models.BooleanField(default=False),
        ),
    ]

