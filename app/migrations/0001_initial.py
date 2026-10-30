# Initial migration for MyKeep models
import django.db.models.deletion
import django.utils.timezone
from decimal import Decimal
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Category',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=80)),
                ('sort_order', models.PositiveIntegerField(default=0)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='categories', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['sort_order', 'name'],
                'unique_together': {('user', 'name')},
            },
        ),
        migrations.CreateModel(
            name='Currency',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=64)),
                ('code', models.CharField(max_length=10)),
                ('symbol', models.CharField(default='$', max_length=10)),
                ('rate', models.DecimalField(decimal_places=6, default=Decimal('1.000000'), max_digits=14)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='currencies', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['code'],
                'unique_together': {('user', 'code')},
            },
        ),
        migrations.CreateModel(
            name='HouseholdMember',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=80)),
                ('email', models.EmailField(blank=True, default='', max_length=254)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='household_members', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['id'],
            },
        ),
        migrations.CreateModel(
            name='PaymentMethod',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=80)),
                ('icon', models.CharField(blank=True, default='', max_length=120)),
                ('enabled', models.BooleanField(default=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='payment_methods', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['name'],
            },
        ),
        migrations.CreateModel(
            name='Subscription',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=120)),
                ('price', models.DecimalField(decimal_places=2, max_digits=12)),
                ('cycle', models.PositiveSmallIntegerField(choices=[(1, 'Daily'), (2, 'Weekly'), (3, 'Monthly'), (4, 'Yearly'), (5, 'One-Time')], default=3)),
                ('frequency', models.PositiveSmallIntegerField(default=1)),
                ('next_payment', models.DateField(default=django.utils.timezone.now)),
                ('start_date', models.DateField(blank=True, null=True)),
                ('cancellation_date', models.DateField(blank=True, null=True)),
                ('auto_renew', models.BooleanField(default=True)),
                ('inactive', models.BooleanField(default=False)),
                ('notify', models.BooleanField(default=True)),
                ('notify_days_before', models.PositiveSmallIntegerField(default=3)),
                ('logo_url', models.CharField(blank=True, default='', max_length=500)),
                ('url', models.URLField(blank=True, default='', max_length=500)),
                ('notes', models.TextField(blank=True, default='')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('categories', models.ManyToManyField(blank=True, related_name='multi_subscriptions', to='app.category')),
                ('category', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='subscriptions', to='app.category')),
                ('currency', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='subscriptions', to='app.currency')),
                ('payer', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='subscriptions', to='app.householdmember')),
                ('payers', models.ManyToManyField(blank=True, related_name='shared_subscriptions', to='app.householdmember')),
                ('payment_method', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='subscriptions', to='app.paymentmethod')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='subscriptions', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['next_payment', 'name'],
            },
        ),
        migrations.CreateModel(
            name='SubscriptionPaymentRecord',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('paid_date', models.DateField(default=django.utils.timezone.now)),
                ('amount', models.DecimalField(decimal_places=2, max_digits=12)),
                ('currency_code', models.CharField(default='USD', max_length=10)),
                ('notes', models.CharField(blank=True, default='', max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('subscription', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='payment_records', to='app.subscription')),
            ],
            options={
                'ordering': ['-paid_date', '-id'],
            },
        ),
        migrations.CreateModel(
            name='WishlistItem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=150)),
                ('price', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12)),
                ('saved_amount', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12)),
                ('priority', models.CharField(choices=[('high', 'High Priority'), ('medium', 'Medium Priority'), ('low', 'Low Priority')], default='medium', max_length=16)),
                ('image_url', models.CharField(blank=True, default='', max_length=600)),
                ('product_url', models.URLField(blank=True, default='', max_length=600)),
                ('target_date', models.DateField(blank=True, null=True)),
                ('notes', models.TextField(blank=True, default='')),
                ('is_purchased', models.BooleanField(default=False)),
                ('purchased_at', models.DateField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('category', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='wishlist_items', to='app.category')),
                ('currency', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='wishlist_items', to='app.currency')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='wishlist_items', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['is_purchased', '-created_at'],
            },
        ),
        migrations.CreateModel(
            name='UserSettings',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('monthly_budget', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12)),
                ('theme', models.CharField(choices=[('light', 'Light'), ('dark', 'Dark'), ('system', 'System')], default='light', max_length=16)),
                ('show_monthly_price', models.BooleanField(default=True)),
                ('show_original_price', models.BooleanField(default=True)),
                ('hide_disabled', models.BooleanField(default=False)),
                ('exchange_provider', models.CharField(choices=[('frankfurter', 'Frankfurter (Free)'), ('fixer', 'Fixer API')], default='frankfurter', max_length=24)),
                ('fixer_api_key', models.CharField(blank=True, default='', max_length=128)),
                ('last_exchange_update', models.DateTimeField(blank=True, null=True)),
                ('api_key', models.CharField(blank=True, max_length=64, unique=True)),
                ('ai_enabled', models.BooleanField(default=False)),
                ('ai_provider', models.CharField(blank=True, default='openai', max_length=32)),
                ('ai_model', models.CharField(blank=True, default='gpt-4o-mini', max_length=64)),
                ('ai_api_key', models.CharField(blank=True, default='', max_length=255)),
                ('ai_base_url', models.URLField(blank=True, default='')),
                ('main_currency', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='app.currency')),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='settings', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='NotificationConfig',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('days_before', models.PositiveSmallIntegerField(default=3)),
                ('email_enabled', models.BooleanField(default=False)),
                ('smtp_host', models.CharField(blank=True, default='', max_length=255)),
                ('smtp_port', models.PositiveIntegerField(default=587)),
                ('smtp_username', models.CharField(blank=True, default='', max_length=255)),
                ('smtp_password', models.CharField(blank=True, default='', max_length=255)),
                ('smtp_from_email', models.EmailField(blank=True, default='', max_length=254)),
                ('discord_enabled', models.BooleanField(default=False)),
                ('discord_webhook_url', models.URLField(blank=True, default='', max_length=500)),
                ('telegram_enabled', models.BooleanField(default=False)),
                ('telegram_bot_token', models.CharField(blank=True, default='', max_length=255)),
                ('telegram_chat_id', models.CharField(blank=True, default='', max_length=128)),
                ('gotify_enabled', models.BooleanField(default=False)),
                ('gotify_url', models.URLField(blank=True, default='', max_length=500)),
                ('gotify_token', models.CharField(blank=True, default='', max_length=255)),
                ('pushover_enabled', models.BooleanField(default=False)),
                ('pushover_user_key', models.CharField(blank=True, default='', max_length=255)),
                ('pushover_api_token', models.CharField(blank=True, default='', max_length=255)),
                ('ntfy_enabled', models.BooleanField(default=False)),
                ('ntfy_url', models.URLField(blank=True, default='', max_length=500)),
                ('ntfy_topic', models.CharField(blank=True, default='', max_length=128)),
                ('webhook_enabled', models.BooleanField(default=False)),
                ('webhook_url', models.URLField(blank=True, default='', max_length=500)),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='notification_config', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='AIRecommendation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(max_length=200)),
                ('description', models.TextField()),
                ('savings', models.CharField(blank=True, default='', max_length=80)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='ai_recommendations', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
    ]
