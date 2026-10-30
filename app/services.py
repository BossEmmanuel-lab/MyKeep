import ipaddress
import json
import os
import smtplib
import socket
import uuid
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from .models import (
    AIRecommendation,
    Category,
    Currency,
    HouseholdMember,
    NotificationConfig,
    PaymentMethod,
    Subscription,
    UserSettings,
    WishlistItem,
    YearlyCostSnapshot,
)

DEFAULT_CURRENCIES = [
    ('US Dollar', 'USD', '$', Decimal('1.000000')),
    ('Euro', 'EUR', '€', Decimal('0.920000')),
    ('British Pound', 'GBP', '£', Decimal('0.790000')),
    ('Canadian Dollar', 'CAD', 'CA$', Decimal('1.360000')),
    ('Japanese Yen', 'JPY', '¥', Decimal('152.000000')),
    ('Nigerian Naira', 'NGN', '₦', Decimal('1600.000000')),
]

DEFAULT_CATEGORIES = [
    'Entertainment',
    'Development',
    'Music',
    'Infrastructure',
    'Productivity',
    'Utilities',
    'Hardware & Tech',
    'Home & Office',
]

DEFAULT_PAYMENT_METHODS = [
    'Credit Card',
    'Debit Card',
    'PayPal',
    'Apple Pay',
    'Bank Transfer',
]


def bootstrap_user_defaults(user):
    # Ensure a newly created user has default currencies, categories, household, payment methods, and settings
    created_currencies = {}
    for name, code, symbol, rate in DEFAULT_CURRENCIES:
        curr, _ = Currency.objects.get_or_create(
            user=user,
            code=code,
            defaults={'name': name, 'symbol': symbol, 'rate': rate},
        )
        created_currencies[code] = curr

    for idx, cat_name in enumerate(DEFAULT_CATEGORIES):
        Category.objects.get_or_create(
            user=user,
            name=cat_name,
            defaults={'sort_order': idx},
        )

    for pm_name in DEFAULT_PAYMENT_METHODS:
        PaymentMethod.objects.get_or_create(
            user=user,
            name=pm_name,
        )

    if not HouseholdMember.objects.filter(user=user).exists():
        HouseholdMember.objects.create(
            user=user,
            name=user.first_name or user.username,
            email=user.email or '',
        )

    user_settings, _ = UserSettings.objects.get_or_create(
        user=user,
        defaults={
            'main_currency': created_currencies.get('USD'),
            'monthly_budget': Decimal('200.00'),
            'theme': 'light',
        },
    )
    if not user_settings.main_currency:
        user_settings.main_currency = created_currencies.get('USD') or Currency.objects.filter(user=user).first()
        user_settings.save(update_fields=['main_currency'])

    NotificationConfig.objects.get_or_create(user=user)
    return user_settings


def convert_amount(amount, from_currency, to_currency):
    # Convert an amount between two Currency instances using their rates relative to base
    amt = Decimal(str(amount or '0'))
    if not from_currency or not to_currency or from_currency.id == to_currency.id:
        return amt.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    from_rate = Decimal(str(from_currency.rate or '1'))
    to_rate = Decimal(str(to_currency.rate or '1'))
    if from_rate <= 0:
        from_rate = Decimal('1')
    if to_rate <= 0:
        to_rate = Decimal('1')

    converted = (amt / from_rate) * to_rate
    return converted.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def format_money(amount, currency=None):
    amt = Decimal(str(amount or '0')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    symbol = currency.symbol if currency and currency.symbol else '$'
    return f"{symbol}{amt:,.2f}"


def is_safe_public_url(url):
    # Prevent SSRF against loopback, link-local, RFC1918, and CGNAT ranges unless explicitly allowed
    if not url:
        return False
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname:
        return False

    allowlist_raw = os.environ.get('SSRF_ALLOWLIST', '')
    allowlist = {h.strip().lower() for h in allowlist_raw.split(',') if h.strip()}
    hostname = parsed.hostname.lower()
    if hostname in allowlist:
        return True

    try:
        infos = socket.getaddrinfo(hostname, parsed.port or (443 if parsed.scheme == 'https' else 80))
    except socket.gaierror:
        return False

    for info in infos:
        ip_str = info[4][0]
        try:
            ip_obj = ipaddress.ip_address(ip_str)
            if (
                ip_obj.is_private
                or ip_obj.is_loopback
                or ip_obj.is_link_local
                or ip_obj.is_multicast
                or ip_obj.is_reserved
                or ip_obj.is_unspecified
            ):
                return False
        except ValueError:
            return False

    return True


from .security import validate_and_sanitize_image


def store_uploaded_file(uploaded_file, folder='uploads'):
    # Validate magic bytes, strip EXIF, re-encode image safely, and store on local disk
    clean_bytes, ext = validate_and_sanitize_image(uploaded_file)
    local_name = f"{uuid.uuid4().hex}{ext}"

    target_dir = Path(settings.MEDIA_ROOT) / folder
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / local_name
    target_path.write_bytes(clean_bytes)
    return f"{settings.MEDIA_URL.rstrip('/')}/{folder}/{local_name}"


def update_exchange_rates_for_user(user):
    # Fetch latest rates from Frankfurter or Fixer and update user's currencies
    user_settings = bootstrap_user_defaults(user)
    main_curr = user_settings.main_currency
    base_code = main_curr.code if main_curr else 'USD'

    cache_key = f"fx_rates:{user_settings.exchange_provider}:{base_code}"
    rates = cache.get(cache_key)

    if not rates:
        if user_settings.exchange_provider == 'fixer' and user_settings.fixer_api_key:
            url = f"http://data.fixer.io/api/latest?access_key={user_settings.fixer_api_key}"
        else:
            url = f"https://api.frankfurter.app/latest?from={base_code}"

        req = Request(url, headers={'User-Agent': 'MyKeep/1.0'})
        with urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode('utf-8'))
            rates = payload.get('rates', {})
            rates[base_code] = 1.0
            cache.set(cache_key, rates, timeout=3600 * 6)

    updated = 0
    for curr in Currency.objects.filter(user=user):
        if curr.code == base_code:
            curr.rate = Decimal('1.000000')
            curr.save(update_fields=['rate', 'updated_at'])
            updated += 1
        elif curr.code in rates:
            curr.rate = Decimal(str(rates[curr.code])).quantize(Decimal('0.000001'))
            curr.save(update_fields=['rate', 'updated_at'])
            updated += 1

    user_settings.last_exchange_update = timezone.now()
    user_settings.save(update_fields=['last_exchange_update'])
    cache.delete(f"dashboard_metrics:{user.id}")
    return updated


def compute_dashboard_metrics(user):
    # Compute summary metrics, upcoming renewals, overdue renewals, and wishlist totals
    user_settings = bootstrap_user_defaults(user)
    main_curr = user_settings.main_currency

    active_subs = list(
        Subscription.objects.filter(user=user, inactive=False)
        .select_related('currency', 'category', 'payer', 'payment_method')
    )
    inactive_subs = list(
        Subscription.objects.filter(user=user, inactive=True)
        .select_related('currency')
    )

    monthly_total = Decimal('0.00')
    due_next_7_days = Decimal('0.00')
    due_next_7_count = 0
    today = date.today()
    seven_days = today + timedelta(days=7)
    thirty_days = today + timedelta(days=30)

    upcoming_renewals = []
    overdue_renewals = []
    upcoming_cancellations = []
    category_totals = {}
    member_totals = {}
    payment_method_totals = {}
    most_expensive_sub = None
    most_expensive_monthly = Decimal('0.00')

    for sub in active_subs:
        converted_price = convert_amount(sub.price, sub.currency, main_curr)
        sub.converted_price = converted_price
        sub.formatted_price = format_money(sub.price, sub.currency)
        sub.formatted_converted_price = format_money(converted_price, main_curr)
        sub.is_different_currency = bool(sub.currency and main_curr and sub.currency.id != main_curr.id)

        monthly_cost = (converted_price * sub.monthly_multiplier()).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )
        sub.monthly_equivalent = monthly_cost
        monthly_total += monthly_cost

        if monthly_cost >= most_expensive_monthly:
            most_expensive_monthly = monthly_cost
            most_expensive_sub = sub

        cat_name = sub.category.name if sub.category else 'Uncategorized'
        category_totals[cat_name] = category_totals.get(cat_name, Decimal('0.00')) + monthly_cost

        member_name = sub.payer.name if sub.payer else 'Default'
        member_totals[member_name] = member_totals.get(member_name, Decimal('0.00')) + monthly_cost

        pm_name = sub.payment_method.name if sub.payment_method else 'Unspecified'
        payment_method_totals[pm_name] = payment_method_totals.get(pm_name, Decimal('0.00')) + monthly_cost

        if sub.cancellation_date and sub.cancellation_date >= today:
            upcoming_cancellations.append(sub)

        if sub.next_payment:
            if sub.next_payment < today and not sub.auto_renew and sub.cycle != Subscription.CYCLE_ONCE:
                overdue_renewals.append(sub)
            elif today <= sub.next_payment <= thirty_days:
                upcoming_renewals.append(sub)
            if today <= sub.next_payment <= seven_days:
                due_next_7_days += converted_price
                due_next_7_count += 1

    upcoming_renewals.sort(key=lambda s: s.next_payment)
    overdue_renewals.sort(key=lambda s: s.next_payment)
    upcoming_cancellations.sort(key=lambda s: s.cancellation_date)

    monthly_savings = Decimal('0.00')
    for sub in inactive_subs:
        converted_price = convert_amount(sub.price, sub.currency, main_curr)
        monthly_savings += (converted_price * sub.monthly_multiplier()).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )

    wishlist_qs = list(
        WishlistItem.objects.filter(user=user, is_purchased=False).select_related('currency', 'category')
    )
    wishlist_total = Decimal('0.00')
    wishlist_saved_total = Decimal('0.00')
    for item in wishlist_qs:
        item.converted_price = convert_amount(item.price, item.currency, main_curr)
        item.converted_saved = convert_amount(item.saved_amount, item.currency, main_curr)
        item.formatted_price = format_money(item.price, item.currency)
        item.formatted_converted_price = format_money(item.converted_price, main_curr)
        wishlist_total += item.converted_price
        wishlist_saved_total += item.converted_saved

    yearly_projection = (monthly_total * Decimal('12')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    budget = user_settings.monthly_budget or Decimal('0.00')
    budget_remaining = max(budget - monthly_total, Decimal('0.00'))

    chart_palette = [
        '#0071E3',
        '#5E5CE6',
        '#30D158',
        '#FF9F0A',
        '#BF5AF2',
        '#FF375F',
        '#64D2FF',
        '#AC8E68',
    ]

    def build_breakdown_list(totals_dict):
        rows = []
        circumference = 251.327  # 2 * pi * 40
        cumulative_pct = 0.0
        for idx, (k, v) in enumerate(sorted(totals_dict.items(), key=lambda x: x[1], reverse=True)):
            exact_pct = float((v / monthly_total * Decimal('100'))) if monthly_total > 0 else 0.0
            share_pct = int(round(exact_pct))
            seg_len = round((exact_pct / 100.0) * circumference, 2)
            dash_offset = round(-((cumulative_pct / 100.0) * circumference), 2)
            cumulative_pct += exact_pct
            rows.append({
                'name': k,
                'monthly': v,
                'yearly': (v * Decimal('12')).quantize(Decimal('0.01')),
                'formatted': format_money(v, main_curr),
                'formatted_yearly': format_money(v * Decimal('12'), main_curr),
                'share_pct': share_pct,
                'exact_pct': round(exact_pct, 1),
                'color': chart_palette[idx % len(chart_palette)],
                'dash_array': f"{max(seg_len - 2.0, 1.0 if seg_len > 0 else 0.0)} {circumference}",
                'dash_offset': dash_offset,
            })
        return rows

    sorted_categories = build_breakdown_list(category_totals)
    sorted_members = build_breakdown_list(member_totals)
    sorted_payment_methods = build_breakdown_list(payment_method_totals)

    # Compute 12-month cashflow projection across upcoming calendar months
    month_names = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
    cashflow_months = []
    for offset in range(12):
        m_num = ((today.month - 1 + offset) % 12) + 1
        y_num = today.year + ((today.month - 1 + offset) // 12)
        m_total = Decimal('0.00')
        for sub in active_subs:
            conv = sub.converted_price
            if sub.cycle == Subscription.CYCLE_MONTHLY and sub.frequency == 1:
                m_total += conv
            elif sub.cycle == Subscription.CYCLE_WEEKLY:
                m_total += (conv * Decimal('4.33') / Decimal(max(sub.frequency, 1))).quantize(Decimal('0.01'))
            elif sub.cycle == Subscription.CYCLE_DAILY:
                m_total += (conv * Decimal('30') / Decimal(max(sub.frequency, 1))).quantize(Decimal('0.01'))
            elif sub.cycle == Subscription.CYCLE_YEARLY:
                if sub.next_payment and sub.next_payment.month == m_num:
                    m_total += conv
            elif sub.cycle == Subscription.CYCLE_MONTHLY and sub.frequency > 1:
                if sub.next_payment:
                    diff_months = (y_num - sub.next_payment.year) * 12 + (m_num - sub.next_payment.month)
                    if diff_months >= 0 and diff_months % sub.frequency == 0:
                        m_total += conv
        cashflow_months.append({
            'label': month_names[m_num - 1],
            'year': y_num,
            'amount': m_total.quantize(Decimal('0.01')),
            'formatted': format_money(m_total, main_curr),
        })

    max_cf = max((m['amount'] for m in cashflow_months), default=Decimal('1.00'))
    if max_cf <= 0:
        max_cf = Decimal('1.00')

    chart_w = 660.0
    chart_h = 160.0
    pad_top = 20.0
    usable_h = chart_h - pad_top - 20.0
    step_x = chart_w / 11.0
    pts = []
    for i, m in enumerate(cashflow_months):
        ratio = float(m['amount'] / max_cf) if max_cf > 0 else 0.0
        m['height_pct'] = max(int(round(ratio * 100)), 8 if m['amount'] > 0 else 4)
        cx = round(i * step_x, 1)
        cy = round(pad_top + usable_h * (1.0 - ratio), 1)
        m['cx'] = cx
        m['cy'] = cy
        pts.append((cx, cy))

    # Build smooth cubic bezier SVG path for cashflow trend line & area
    if pts:
        d_parts = [f"M {pts[0][0]} {pts[0][1]}"]
        for i in range(len(pts) - 1):
            x0, y0 = pts[i]
            x1, y1 = pts[i + 1]
            cpx1 = round(x0 + (x1 - x0) * 0.45, 1)
            cpx2 = round(x1 - (x1 - x0) * 0.45, 1)
            d_parts.append(f"C {cpx1} {y0}, {cpx2} {y1}, {x1} {y1}")
        cashflow_svg_line = ' '.join(d_parts)
        cashflow_svg_area = f"{cashflow_svg_line} L {pts[-1][0]} {chart_h} L {pts[0][0]} {chart_h} Z"
    else:
        cashflow_svg_line = ''
        cashflow_svg_area = ''

    average_monthly_per_sub = (
        (monthly_total / Decimal(len(active_subs))).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        if active_subs
        else Decimal('0.00')
    )
    over_budget_amount = max(monthly_total - budget, Decimal('0.00')) if budget > 0 else Decimal('0.00')
    budget_used_pct = (
        ((monthly_total / budget) * Decimal('100')).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)
        if budget > 0
        else Decimal('0.0')
    )

    return {
        'main_currency': main_curr,
        'monthly_total': monthly_total,
        'formatted_monthly_total': format_money(monthly_total, main_curr),
        'yearly_projection': yearly_projection,
        'formatted_yearly_projection': format_money(yearly_projection, main_curr),
        'monthly_budget': budget,
        'formatted_monthly_budget': format_money(budget, main_curr),
        'budget_remaining': budget_remaining,
        'formatted_budget_remaining': format_money(budget_remaining, main_curr),
        'over_budget_amount': over_budget_amount,
        'formatted_over_budget_amount': format_money(over_budget_amount, main_curr),
        'budget_used_pct': budget_used_pct,
        'active_count': len(active_subs),
        'inactive_count': len(inactive_subs),
        'monthly_savings': monthly_savings,
        'formatted_monthly_savings': format_money(monthly_savings, main_curr),
        'formatted_yearly_savings': format_money(monthly_savings * Decimal('12'), main_curr),
        'average_monthly_per_sub': average_monthly_per_sub,
        'formatted_average_monthly_per_sub': format_money(average_monthly_per_sub, main_curr),
        'most_expensive_sub': most_expensive_sub,
        'formatted_most_expensive_monthly': format_money(most_expensive_monthly, main_curr),
        'due_next_7_days': due_next_7_days,
        'formatted_due_next_7_days': format_money(due_next_7_days, main_curr),
        'due_next_7_count': due_next_7_count,
        'upcoming_renewals': upcoming_renewals,
        'overdue_renewals': overdue_renewals,
        'upcoming_cancellations': upcoming_cancellations,
        'wishlist_items': wishlist_qs,
        'wishlist_count': len(wishlist_qs),
        'wishlist_total': wishlist_total,
        'formatted_wishlist_total': format_money(wishlist_total, main_curr),
        'wishlist_saved_total': wishlist_saved_total,
        'formatted_wishlist_saved_total': format_money(wishlist_saved_total, main_curr),
        'category_breakdown': sorted_categories,
        'member_breakdown': sorted_members,
        'payment_method_breakdown': sorted_payment_methods,
        'cashflow_months': cashflow_months,
        'cashflow_svg_line': cashflow_svg_line,
        'cashflow_svg_area': cashflow_svg_area,
    }


def generate_ical_feed(user):
    # Build RFC 5545 iCalendar feed for active subscriptions and target wishlist dates
    lines = [
        'BEGIN:VCALENDAR',
        'VERSION:2.0',
        'PRODID:-//MyKeep//Subscription & Wishlist Tracker//EN',
        'CALSCALE:GREGORIAN',
        'METHOD:PUBLISH',
    ]
    subs = Subscription.objects.filter(user=user, inactive=False).select_related('currency')
    for sub in subs:
        if not sub.next_payment:
            continue
        dt_str = sub.next_payment.strftime('%Y%m%d')
        price_str = format_money(sub.price, sub.currency)
        lines.extend([
            'BEGIN:VEVENT',
            f'UID:mykeep-sub-{sub.id}@mykeep.local',
            f'DTSTART;VALUE=DATE:{dt_str}',
            f'DTEND;VALUE=DATE:{dt_str}',
            f'SUMMARY:{sub.name} ({price_str})',
            f'DESCRIPTION:Billing Cycle: {sub.cycle_label}\\nNotes: {sub.notes}',
            'END:VEVENT',
        ])

    wishes = WishlistItem.objects.filter(user=user, is_purchased=False, target_date__isnull=False).select_related('currency')
    for item in wishes:
        dt_str = item.target_date.strftime('%Y%m%d')
        price_str = format_money(item.price, item.currency)
        lines.extend([
            'BEGIN:VEVENT',
            f'UID:mykeep-wish-{item.id}@mykeep.local',
            f'DTSTART;VALUE=DATE:{dt_str}',
            f'DTEND;VALUE=DATE:{dt_str}',
            f'SUMMARY:Wishlist Target: {item.name} ({price_str})',
            f'DESCRIPTION:Store URL: {item.product_url}\\nNotes: {item.notes}',
            'END:VEVENT',
        ])

    lines.append('END:VCALENDAR')
    return '\r\n'.join(lines)


def dispatch_notification(user, title, message):
    # Dispatch notification across enabled channels for the user
    cfg, _ = NotificationConfig.objects.get_or_create(user=user)
    sent_channels = []

    if cfg.email_enabled and cfg.smtp_host and user.email:
        try:
            msg = EmailMessage()
            msg['Subject'] = title
            msg['From'] = cfg.smtp_from_email or cfg.smtp_username or 'noreply@mykeep.local'
            msg['To'] = user.email
            msg.set_content(message)
            with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=10) as server:
                server.ehlo()
                try:
                    server.starttls()
                except Exception:
                    pass
                if cfg.smtp_username and cfg.smtp_password:
                    server.login(cfg.smtp_username, cfg.smtp_password)
                server.send_message(msg)
            sent_channels.append('email')
        except Exception:
            pass

    if cfg.discord_enabled and is_safe_public_url(cfg.discord_webhook_url):
        try:
            data = json.dumps({'content': f"**{title}**\n{message}"}).encode('utf-8')
            req = Request(cfg.discord_webhook_url, data=data, headers={'Content-Type': 'application/json'})
            urlopen(req, timeout=8).read()
            sent_channels.append('discord')
        except Exception:
            pass

    if cfg.telegram_enabled and cfg.telegram_bot_token and cfg.telegram_chat_id:
        try:
            tg_url = f"https://api.telegram.org/bot{cfg.telegram_bot_token}/sendMessage"
            data = json.dumps({'chat_id': cfg.telegram_chat_id, 'text': f"{title}\n{message}"}).encode('utf-8')
            req = Request(tg_url, data=data, headers={'Content-Type': 'application/json'})
            urlopen(req, timeout=8).read()
            sent_channels.append('telegram')
        except Exception:
            pass

    if cfg.webhook_enabled and is_safe_public_url(cfg.webhook_url):
        try:
            data = json.dumps({'title': title, 'message': message}).encode('utf-8')
            req = Request(cfg.webhook_url, data=data, headers={'Content-Type': 'application/json'})
            urlopen(req, timeout=8).read()
            sent_channels.append('webhook')
        except Exception:
            pass

    if cfg.gotify_enabled and is_safe_public_url(cfg.gotify_url) and cfg.gotify_token:
        try:
            gotify_endpoint = f"{cfg.gotify_url.rstrip('/')}/message?token={cfg.gotify_token}"
            data = json.dumps({'title': title, 'message': message, 'priority': 5}).encode('utf-8')
            req = Request(gotify_endpoint, data=data, headers={'Content-Type': 'application/json'})
            urlopen(req, timeout=8).read()
            sent_channels.append('gotify')
        except Exception:
            pass

    if cfg.pushover_enabled and cfg.pushover_user_key and cfg.pushover_api_token:
        try:
            po_url = 'https://api.pushover.net/1/messages.json'
            data = json.dumps({
                'token': cfg.pushover_api_token,
                'user': cfg.pushover_user_key,
                'title': title,
                'message': message,
            }).encode('utf-8')
            req = Request(po_url, data=data, headers={'Content-Type': 'application/json'})
            urlopen(req, timeout=8).read()
            sent_channels.append('pushover')
        except Exception:
            pass

    if cfg.ntfy_enabled and is_safe_public_url(cfg.ntfy_url) and cfg.ntfy_topic:
        try:
            ntfy_endpoint = f"{cfg.ntfy_url.rstrip('/')}/{cfg.ntfy_topic.lstrip('/')}"
            req = Request(ntfy_endpoint, data=message.encode('utf-8'), headers={'Title': title})
            urlopen(req, timeout=8).read()
            sent_channels.append('ntfy')
        except Exception:
            pass

    return sent_channels


def record_yearly_cost_snapshot(user):
    # Record a historical snapshot of monthly and yearly recurring spend (mirrors Wallos storetotalyearlycost)
    metrics = compute_dashboard_metrics(user)
    snapshot, _ = YearlyCostSnapshot.objects.update_or_create(
        user=user,
        recorded_date=date.today(),
        defaults={
            'total_monthly': metrics['monthly_total'],
            'total_yearly': metrics['yearly_projection'],
            'active_count': metrics['active_count'],
        },
    )
    return snapshot


def generate_local_recommendations(user):
    # Generate actionable cost optimization recommendations locally or via configured AI provider
    metrics = compute_dashboard_metrics(user)
    main_curr = metrics['main_currency']
    subs = Subscription.objects.filter(user=user, inactive=False).select_related('currency', 'category')

    AIRecommendation.objects.filter(user=user).delete()
    created = []

    by_category = {}
    for sub in subs:
        cat_name = sub.category.name if sub.category else 'Other'
        by_category.setdefault(cat_name, []).append(sub)

    for cat_name, items in by_category.items():
        if len(items) >= 2:
            total_cat = sum(
                convert_amount(s.price, s.currency, main_curr) * s.monthly_multiplier()
                for s in items
            )
            names = ', '.join(s.name for s in items[:3])
            rec = AIRecommendation.objects.create(
                user=user,
                title=f"Consolidate {len(items)} {cat_name} subscriptions",
                description=f"You have multiple active services in {cat_name} ({names}). Rotating or consolidating could lower monthly burn.",
                savings=f"{format_money(total_cat * Decimal('0.35'), main_curr)}/mo",
            )
            created.append(rec)

    monthly_subs = [s for s in subs if s.cycle == Subscription.CYCLE_MONTHLY and s.frequency == 1]
    for sub in monthly_subs[:2]:
        conv = convert_amount(sub.price, sub.currency, main_curr)
        annual_save = (conv * Decimal('2')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        if annual_save > 0:
            rec = AIRecommendation.objects.create(
                user=user,
                title=f"Consider annual billing for {sub.name}",
                description=f"Switching {sub.name} from monthly to annual billing typically saves ~16% (2 months free).",
                savings=f"{format_money(annual_save, main_curr)}/yr",
            )
            created.append(rec)

    return created
