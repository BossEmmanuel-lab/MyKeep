from datetime import date, timedelta
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from app.models import NotificationConfig, Subscription, SubscriptionPaymentRecord
from app.services import (
    dispatch_notification,
    format_money,
    record_yearly_cost_snapshot,
    update_exchange_rates_for_user,
)


class Command(BaseCommand):
    help = 'Advance auto-renewing subscriptions, refresh exchange rates, and send due notifications'

    def handle(self, *args, **options):
        today = date.today()
        renewed_count = 0

        # Advance auto-renewing subscriptions whose next_payment has passed
        overdue_auto = Subscription.objects.filter(
            inactive=False,
            auto_renew=True,
            next_payment__lt=today,
        ).exclude(cycle=Subscription.CYCLE_ONCE).select_related('currency')

        for sub in overdue_auto:
            SubscriptionPaymentRecord.objects.create(
                subscription=sub,
                paid_date=sub.next_payment,
                amount=sub.price,
                currency_code=sub.currency.code if sub.currency else 'USD',
                notes='Auto-renewal recorded',
            )
            sub.advance_next_payment(reference_date=today)
            sub.save(update_fields=['next_payment', 'updated_at'])
            renewed_count += 1

        # Send notifications for upcoming renewals
        notified_count = 0
        for user in User.objects.all():
            try:
                update_exchange_rates_for_user(user)
            except Exception:
                pass
            record_yearly_cost_snapshot(user)

            cfg, _ = NotificationConfig.objects.get_or_create(user=user)
            target_date = today + timedelta(days=cfg.days_before)
            due_subs = Subscription.objects.filter(
                user=user,
                inactive=False,
                notify=True,
                next_payment=target_date,
            ).select_related('currency')

            if due_subs.exists():
                lines = [
                    f"- {s.name}: {format_money(s.price, s.currency)} due on {s.next_payment.isoformat()}"
                    for s in due_subs
                ]
                sent = dispatch_notification(
                    user,
                    title='MyKeep: Upcoming Subscription Renewals',
                    message='\n'.join(lines),
                )
                if sent:
                    notified_count += 1

            # Also dispatch cancellation reminders on the scheduled cancellation_date
            cancel_subs = Subscription.objects.filter(
                user=user,
                inactive=False,
                notify=True,
                cancellation_date=today,
            ).select_related('currency')
            if cancel_subs.exists():
                c_lines = [
                    f"- Cancel {s.name} ({format_money(s.price, s.currency)}) today"
                    for s in cancel_subs
                ]
                dispatch_notification(
                    user,
                    title='MyKeep: Subscription Cancellation Reminder',
                    message='\n'.join(c_lines),
                )

        self.stdout.write(f"Renewed {renewed_count} subscriptions; notified {notified_count} users.")
