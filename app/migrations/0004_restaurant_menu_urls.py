from django.db import migrations, models


def copy_primary_to_menu_urls(apps, schema_editor):
    Restaurant = apps.get_model("app", "Restaurant")
    for restaurant in Restaurant.objects.all():
        urls = []
        if restaurant.primary_menu_url:
            urls.append(restaurant.primary_menu_url)
        restaurant.menu_urls = urls
        restaurant.save(update_fields=["menu_urls"])


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0003_concept_subtitle_alter_concept_name"),
    ]

    operations = [
        migrations.AddField(
            model_name="restaurant",
            name="menu_urls",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.RunPython(copy_primary_to_menu_urls, migrations.RunPython.noop),
    ]
