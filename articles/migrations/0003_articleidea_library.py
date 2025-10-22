from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("articles", "0002_seed_sample_articles"),
    ]

    operations = [
        migrations.CreateModel(
            name="ArticleIdea",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("title", models.CharField(max_length=255)),
                ("subtitle", models.TextField(blank=True)),
                ("angle", models.TextField(blank=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to="auth.user")),
                ("source_run", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to="articles.articlerun")),
            ],
            options={"ordering": ["-created_at", "-id"]},
        ),
    ]

