from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0007_userprofile_stripe_customer_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="default_view",
            field=models.CharField(
                choices=[("list", "List"), ("calendar", "Calendar")],
                default="list",
                max_length=10,
            ),
        ),
    ]
