from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0007_dishidea_is_deleted"),
    ]

    operations = [
        migrations.AddField(
            model_name="account",
            name="stripe_customer_id",
            field=models.TextField(blank=True, null=True),
        ),
    ]
