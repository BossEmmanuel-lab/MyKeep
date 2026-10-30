# Migration 0002: Add 2FA, recovery codes, profile/budget fields, and YearlyCostSnapshot
import django.db.models.deletion
import django.utils.timezone
from decimal import Decimal
from django.conf import settings
from django.db import migrations, models


def apply_schema_idempotently(apps, schema_editor):
    # Safely add missing columns and table if not already created by SQLite connection hook
    connection = schema_editor.connection
    with connection.cursor() as cursor:
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='app_usersettings'")
        if cursor.fetchone():
            cursor.execute('PRAGMA table_info(app_usersettings)')
            existing_cols = {row[1] for row in cursor.fetchall()}
            extra_cols = [
                ('totp_enabled', "BOOLEAN NOT NULL DEFAULT 0"),
                ('totp_secret', "VARCHAR(64) NOT NULL DEFAULT ''"),
                ('recovery_codes_hashes', "TEXT NOT NULL DEFAULT '[]'"),
                ('avatar_url', "VARCHAR(500) NOT NULL DEFAULT ''"),
                ('budget_period_days', "SMALLINT UNSIGNED NOT NULL DEFAULT 30"),
                ('budget_start_day', "SMALLINT UNSIGNED NOT NULL DEFAULT 1"),
            ]
            for col_name, col_def in extra_cols:
                if col_name not in existing_cols:
                    cursor.execute(f"ALTER TABLE app_usersettings ADD COLUMN {col_name} {col_def}")
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS app_yearlycostsnapshot (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recorded_date DATE NOT NULL,
                total_monthly DECIMAL NOT NULL DEFAULT 0,
                total_yearly DECIMAL NOT NULL DEFAULT 0,
                active_count INTEGER UNSIGNED NOT NULL DEFAULT 0,
                user_id INTEGER NOT NULL REFERENCES auth_user(id) DEFERRABLE INITIALLY DEFERRED
            )
            """
        )


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('app', '0001_initial'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(apply_schema_idempotently, migrations.RunPython.noop),
            ],
            state_operations=[
                migrations.AddField(
                    model_name='usersettings',
                    name='totp_enabled',
                    field=models.BooleanField(default=False),
                ),
                migrations.AddField(
                    model_name='usersettings',
                    name='totp_secret',
                    field=models.CharField(blank=True, default='', max_length=64),
                ),
                migrations.AddField(
                    model_name='usersettings',
                    name='recovery_codes_hashes',
                    field=models.TextField(blank=True, default='[]'),
                ),
                migrations.AddField(
                    model_name='usersettings',
                    name='avatar_url',
                    field=models.CharField(blank=True, default='', max_length=500),
                ),
                migrations.AddField(
                    model_name='usersettings',
                    name='budget_period_days',
                    field=models.PositiveSmallIntegerField(default=30),
                ),
                migrations.AddField(
                    model_name='usersettings',
                    name='budget_start_day',
                    field=models.PositiveSmallIntegerField(default=1),
                ),
                migrations.CreateModel(
                    name='YearlyCostSnapshot',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('recorded_date', models.DateField(default=django.utils.timezone.now)),
                        ('total_monthly', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12)),
                        ('total_yearly', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12)),
                        ('active_count', models.PositiveIntegerField(default=0)),
                        ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='yearly_cost_snapshots', to=settings.AUTH_USER_MODEL)),
                    ],
                    options={
                        'ordering': ['-recorded_date', '-id'],
                    },
                ),
            ],
        ),
    ]
