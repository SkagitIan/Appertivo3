from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("leads", "0002_leadrun_lead_shortlisted_lead_run"),
    ]

    operations = [
        migrations.AddField(
            model_name="leadrun",
            name="outscraper_job_id",
            field=models.CharField(blank=True, max_length=100, null=True, unique=True),
        ),
    ]
