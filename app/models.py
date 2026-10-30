import secrets
from datetime import date, timedelta
from decimal import Decimal
from urllib.parse import urlparse

from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone


class Currency(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='currencies')
    name = models.CharField(max_length=64)
    code = models.CharField(max_length=10)
    symbol = models.CharField(max_length=10, default='$')
    rate = models.DecimalField(max_digits=14, decimal_places=6, default=Decimal('1.000000'))
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['code']
        unique_together = ('user', 'code')

    def __str__(self):
        return f"{self.code} ({self.symbol})"


class Category(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='categories')
    name = models.CharField(max_length=80)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'name']
        unique_together = ('user', 'name')

    def __str__(self):
        return self.name


class HouseholdMember(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='household_members')
    name = models.CharField(max_length=80)
    email = models.EmailField(blank=True, default='')

    class Meta:
        ordering = ['id']

    def __str__(self):
        return self.name


class PaymentMethod(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='payment_methods')
    name = models.CharField(max_length=80)
    icon = models.CharField(max_length=120, blank=True, default='')
    enabled = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class UserSettings(models.Model):
    THEME_CHOICES = [
        ('light', 'Light'),
        ('dark', 'Dark'),
        ('system', 'System'),
    ]
    EXCHANGE_PROVIDER_CHOICES = [
        ('frankfurter', 'Frankfurter (Free)'),
        ('fixer', 'Fixer API'),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='settings')
    main_currency = models.ForeignKey(
        Currency,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    monthly_budget = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    theme = models.CharField(max_length=16, choices=THEME_CHOICES, default='light')
    show_monthly_price = models.BooleanField(default=True)
    show_original_price = models.BooleanField(default=True)
    hide_disabled = models.BooleanField(default=False)
    exchange_provider = models.CharField(
        max_length=24,
        choices=EXCHANGE_PROVIDER_CHOICES,
        default='frankfurter',
    )
    fixer_api_key = models.CharField(max_length=128, blank=True, default='')
    last_exchange_update = models.DateTimeField(null=True, blank=True)
    api_key = models.CharField(max_length=64, unique=True, blank=True)
    ai_enabled = models.BooleanField(default=False)
    ai_provider = models.CharField(max_length=32, blank=True, default='openai')
    ai_model = models.CharField(max_length=64, blank=True, default='gpt-4o-mini')
    ai_api_key = models.CharField(max_length=255, blank=True, default='')
    ai_base_url = models.URLField(blank=True, default='')
    totp_enabled = models.BooleanField(default=False)
    totp_secret = models.CharField(max_length=64, blank=True, default='')
    recovery_codes_hashes = models.TextField(blank=True, default='[]')
    avatar_url = models.CharField(max_length=500, blank=True, default='')
    budget_period_days = models.PositiveSmallIntegerField(default=30)
    budget_start_day = models.PositiveSmallIntegerField(default=1)

    def save(self, *args, **kwargs):
        if not self.api_key:
            self.api_key = secrets.token_hex(24)
        super().save(*args, **kwargs)

    def regenerate_api_key(self):
        self.api_key = secrets.token_hex(24)
        self.save(update_fields=['api_key'])
        return self.api_key


class Subscription(models.Model):
    CYCLE_DAILY = 1
    CYCLE_WEEKLY = 2
    CYCLE_MONTHLY = 3
    CYCLE_YEARLY = 4
    CYCLE_ONCE = 5

    CYCLE_CHOICES = [
        (CYCLE_DAILY, 'Daily'),
        (CYCLE_WEEKLY, 'Weekly'),
        (CYCLE_MONTHLY, 'Monthly'),
        (CYCLE_YEARLY, 'Yearly'),
        (CYCLE_ONCE, 'One-Time'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='subscriptions')
    name = models.CharField(max_length=120)
    price = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.ForeignKey(
        Currency,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='subscriptions',
    )
    cycle = models.PositiveSmallIntegerField(choices=CYCLE_CHOICES, default=CYCLE_MONTHLY)
    frequency = models.PositiveSmallIntegerField(default=1)
    next_payment = models.DateField(default=timezone.now)
    start_date = models.DateField(null=True, blank=True)
    cancellation_date = models.DateField(null=True, blank=True)
    auto_renew = models.BooleanField(default=True)
    inactive = models.BooleanField(default=False)
    notify = models.BooleanField(default=True)
    notify_days_before = models.PositiveSmallIntegerField(default=3)
    category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='subscriptions',
    )
    categories = models.ManyToManyField(Category, blank=True, related_name='multi_subscriptions')
    payer = models.ForeignKey(
        HouseholdMember,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='subscriptions',
    )
    payers = models.ManyToManyField(HouseholdMember, blank=True, related_name='shared_subscriptions')
    payment_method = models.ForeignKey(
        PaymentMethod,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='subscriptions',
    )
    logo_url = models.CharField(max_length=500, blank=True, default='')
    url = models.URLField(max_length=500, blank=True, default='')
    notes = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['next_payment', 'name']

    def __str__(self):
        return self.name

    @property
    def initials(self):
        parts = [p for p in self.name.strip().split() if p]
        if len(parts) >= 2:
            return (parts[0][0] + parts[1][0]).upper()
        return self.name[:2].upper() if self.name else 'MK'

    @property
    def cycle_label(self):
        base = dict(self.CYCLE_CHOICES).get(self.cycle, 'Monthly')
        if self.cycle == self.CYCLE_ONCE:
            return 'One-Time'
        if self.frequency == 1:
            return base
        unit_map = {
            self.CYCLE_DAILY: 'days',
            self.CYCLE_WEEKLY: 'weeks',
            self.CYCLE_MONTHLY: 'months',
            self.CYCLE_YEARLY: 'years',
        }
        return f"Every {self.frequency} {unit_map.get(self.cycle, 'months')}"

    def monthly_multiplier(self):
        freq = max(int(self.frequency or 1), 1)
        if self.cycle == self.CYCLE_DAILY:
            return Decimal('30') / Decimal(freq)
        if self.cycle == self.CYCLE_WEEKLY:
            return (Decimal('52') / Decimal('12')) / Decimal(freq)
        if self.cycle == self.CYCLE_MONTHLY:
            return Decimal('1') / Decimal(freq)
        if self.cycle == self.CYCLE_YEARLY:
            return Decimal('1') / (Decimal('12') * Decimal(freq))
        return Decimal('0')

    def advance_next_payment(self, reference_date=None):
        ref = reference_date or date.today()
        if self.cycle == self.CYCLE_ONCE:
            return self.next_payment
        current = self.next_payment or ref
        freq = max(int(self.frequency or 1), 1)
        while current <= ref:
            if self.cycle == self.CYCLE_DAILY:
                current = current + timedelta(days=freq)
            elif self.cycle == self.CYCLE_WEEKLY:
                current = current + timedelta(weeks=freq)
            elif self.cycle == self.CYCLE_MONTHLY:
                month = current.month - 1 + freq
                year = current.year + month // 12
                month = month % 12 + 1
                day = min(
                    current.day,
                    [
                        31,
                        29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
                        31,
                        30,
                        31,
                        30,
                        31,
                        31,
                        30,
                        31,
                        30,
                        31,
                    ][month - 1],
                )
                current = date(year, month, day)
            elif self.cycle == self.CYCLE_YEARLY:
                try:
                    current = date(current.year + freq, current.month, current.day)
                except ValueError:
                    current = date(current.year + freq, current.month, 28)
        self.next_payment = current
        return current


class SubscriptionPaymentRecord(models.Model):
    subscription = models.ForeignKey(
        Subscription,
        on_delete=models.CASCADE,
        related_name='payment_records',
    )
    paid_date = models.DateField(default=timezone.now)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency_code = models.CharField(max_length=10, default='USD')
    notes = models.CharField(max_length=255, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-paid_date', '-id']


class WishlistItem(models.Model):
    PRIORITY_LOW = 'low'
    PRIORITY_MEDIUM = 'medium'
    PRIORITY_HIGH = 'high'

    PRIORITY_CHOICES = [
        (PRIORITY_HIGH, 'High Priority'),
        (PRIORITY_MEDIUM, 'Medium Priority'),
        (PRIORITY_LOW, 'Low Priority'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='wishlist_items')
    name = models.CharField(max_length=150)
    price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    saved_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    currency = models.ForeignKey(
        Currency,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='wishlist_items',
    )
    category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='wishlist_items',
    )
    priority = models.CharField(max_length=16, choices=PRIORITY_CHOICES, default=PRIORITY_MEDIUM)
    image_url = models.CharField(max_length=600, blank=True, default='')
    product_url = models.URLField(max_length=600, blank=True, default='')
    target_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True, default='')
    is_purchased = models.BooleanField(default=False)
    purchased_at = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['is_purchased', '-created_at']

    def __str__(self):
        return self.name

    @property
    def domain(self):
        if not self.product_url:
            return ''
        parsed = urlparse(self.product_url)
        host = parsed.netloc.lower()
        return host[4:] if host.startswith('www.') else host

    @property
    def priority_label(self):
        return dict(self.PRIORITY_CHOICES).get(self.priority, 'Medium Priority')


class NotificationConfig(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='notification_config')
    days_before = models.PositiveSmallIntegerField(default=3)
    email_enabled = models.BooleanField(default=False)
    smtp_host = models.CharField(max_length=255, blank=True, default='')
    smtp_port = models.PositiveIntegerField(default=587)
    smtp_username = models.CharField(max_length=255, blank=True, default='')
    smtp_password = models.CharField(max_length=255, blank=True, default='')
    smtp_from_email = models.EmailField(blank=True, default='')
    discord_enabled = models.BooleanField(default=False)
    discord_webhook_url = models.URLField(max_length=500, blank=True, default='')
    telegram_enabled = models.BooleanField(default=False)
    telegram_bot_token = models.CharField(max_length=255, blank=True, default='')
    telegram_chat_id = models.CharField(max_length=128, blank=True, default='')
    gotify_enabled = models.BooleanField(default=False)
    gotify_url = models.URLField(max_length=500, blank=True, default='')
    gotify_token = models.CharField(max_length=255, blank=True, default='')
    pushover_enabled = models.BooleanField(default=False)
    pushover_user_key = models.CharField(max_length=255, blank=True, default='')
    pushover_api_token = models.CharField(max_length=255, blank=True, default='')
    ntfy_enabled = models.BooleanField(default=False)
    ntfy_url = models.URLField(max_length=500, blank=True, default='')
    ntfy_topic = models.CharField(max_length=128, blank=True, default='')
    webhook_enabled = models.BooleanField(default=False)
    webhook_url = models.URLField(max_length=500, blank=True, default='')


class AIRecommendation(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='ai_recommendations')
    title = models.CharField(max_length=200)
    description = models.TextField()
    savings = models.CharField(max_length=80, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class YearlyCostSnapshot(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='yearly_cost_snapshots')
    recorded_date = models.DateField(default=timezone.now)
    total_monthly = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    total_yearly = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    active_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['-recorded_date', '-id']

