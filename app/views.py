import calendar
import csv
import io
import json
import time
from datetime import date, datetime
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from .models import (
    AIRecommendation,
    Category,
    Currency,
    HouseholdMember,
    NotificationConfig,
    PaymentMethod,
    Subscription,
    SubscriptionPaymentRecord,
    UserSettings,
    WishlistItem,
    YearlyCostSnapshot,
)
from .security import (
    CURRENCY_CODE_RE,
    build_otpauth_uri,
    check_rate_limit,
    generate_recovery_codes,
    generate_totp_secret,
    get_client_ip,
    log_security_event,
    rate_limit_exceeded_response,
    reset_rate_limit,
    sanitize_text,
    sanitize_url,
    validate_positive_decimal,
    validate_username,
    verify_and_consume_recovery_code,
    verify_totp_code,
)
from .services import (
    bootstrap_user_defaults,
    compute_dashboard_metrics,
    convert_amount,
    dispatch_notification,
    format_money,
    generate_ical_feed,
    generate_local_recommendations,
    record_yearly_cost_snapshot,
    store_uploaded_file,
    update_exchange_rates_for_user,
)


def _parse_date(val, fallback=None):
    if not val:
        return fallback
    try:
        return datetime.strptime(str(val).strip()[:10], '%Y-%m-%d').date()
    except ValueError:
        return fallback


def _base_context(request, active_nav='dashboard'):
    user_settings = bootstrap_user_defaults(request.user)
    return {
        'active_nav': active_nav,
        'user_settings': user_settings,
        'main_currency': user_settings.main_currency,
        'currencies': Currency.objects.filter(user=request.user),
        'categories': Category.objects.filter(user=request.user),
        'household_members': HouseholdMember.objects.filter(user=request.user),
        'payment_methods': PaymentMethod.objects.filter(user=request.user, enabled=True),
    }


def health_view(request):
    return JsonResponse({'status': 'ok', 'app': 'MyKeep'})


@require_http_methods(['GET', 'POST'])
def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    if not User.objects.exists():
        return redirect('register')

    error = None
    if request.method == 'POST':
        ip = get_client_ip(request)
        raw_username = sanitize_text(request.POST.get('username', ''), max_length=64)
        password = request.POST.get('password', '')

        allowed_ip, _ = check_rate_limit('login_ip', ip, limit=10, window_seconds=300)
        allowed_user, _ = check_rate_limit('login_user', raw_username.lower() or 'blank', limit=6, window_seconds=300)
        if not allowed_ip or not allowed_user:
            log_security_event('login_rate_limited', request=request, details=f'username={raw_username}')
            return rate_limit_exceeded_response('Too many login attempts. Please wait 5 minutes before trying again.')

        user = authenticate(request, username=raw_username, password=password)
        if user is not None and user.is_active:
            user_settings = bootstrap_user_defaults(user)
            if user_settings.totp_enabled and user_settings.totp_secret:
                # Store pre-2FA user in session and redirect to 2FA verification step
                request.session['pre_2fa_user_id'] = user.id
                request.session['pre_2fa_timestamp'] = int(time.time())
                log_security_event('login_password_verified_awaiting_2fa', request=request, user=user)
                return redirect('login_2fa')

            reset_rate_limit('login_ip', ip)
            reset_rate_limit('login_user', raw_username.lower())
            login(request, user)
            log_security_event('login_success', request=request, user=user)
            return redirect('dashboard')

        log_security_event('login_failed', request=request, details=f'username={raw_username}')
        error = 'Invalid username or password.'

    return render(request, 'auth/login.html', {'error': error})


@require_http_methods(['GET', 'POST'])
def login_2fa_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    pre_user_id = request.session.get('pre_2fa_user_id')
    pre_ts = request.session.get('pre_2fa_timestamp', 0)
    if not pre_user_id or (int(time.time()) - int(pre_ts) > 300):
        request.session.pop('pre_2fa_user_id', None)
        request.session.pop('pre_2fa_timestamp', None)
        return redirect('login')

    user = User.objects.filter(id=pre_user_id, is_active=True).first()
    if not user:
        return redirect('login')

    user_settings = bootstrap_user_defaults(user)
    error = None

    if request.method == 'POST':
        ip = get_client_ip(request)
        allowed, _ = check_rate_limit('totp_verify', f"{ip}:{user.id}", limit=6, window_seconds=300)
        if not allowed:
            log_security_event('totp_rate_limited', request=request, user=user)
            return rate_limit_exceeded_response('Too many 2FA verification attempts. Please wait 5 minutes.')

        token = sanitize_text(request.POST.get('code', ''), max_length=32)
        if verify_totp_code(user_settings.totp_secret, token):
            request.session.pop('pre_2fa_user_id', None)
            request.session.pop('pre_2fa_timestamp', None)
            reset_rate_limit('totp_verify', f"{ip}:{user.id}")
            login(request, user)
            log_security_event('login_2fa_totp_success', request=request, user=user)
            return redirect('dashboard')

        # Check if submitted code is a single-use recovery code
        try:
            hashed_list = json.loads(user_settings.recovery_codes_hashes or '[]')
        except json.JSONDecodeError:
            hashed_list = []

        matched, remaining = verify_and_consume_recovery_code(hashed_list, token)
        if matched:
            user_settings.recovery_codes_hashes = json.dumps(remaining)
            user_settings.save(update_fields=['recovery_codes_hashes'])
            request.session.pop('pre_2fa_user_id', None)
            request.session.pop('pre_2fa_timestamp', None)
            reset_rate_limit('totp_verify', f"{ip}:{user.id}")
            login(request, user)
            log_security_event('login_2fa_recovery_code_used', request=request, user=user, details=f'remaining={len(remaining)}')
            messages.warning(request, f'Signed in with a recovery code. You have {len(remaining)} recovery codes left.')
            return redirect('dashboard')

        log_security_event('login_2fa_failed', request=request, user=user)
        error = 'Invalid authenticator code or recovery code.'

    return render(request, 'auth/totp_verify.html', {'error': error, 'username': user.username})


@require_http_methods(['GET', 'POST'])
def register_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    error = None
    if request.method == 'POST':
        ip = get_client_ip(request)
        allowed, _ = check_rate_limit('register_ip', ip, limit=5, window_seconds=600)
        if not allowed:
            log_security_event('register_rate_limited', request=request)
            return rate_limit_exceeded_response('Too many registration attempts. Please try again later.')

        raw_username = request.POST.get('username', '')
        email = sanitize_text(request.POST.get('email', ''), max_length=150)
        password = request.POST.get('password', '')
        main_currency_code = sanitize_text(request.POST.get('main_currency', 'USD'), max_length=10).upper()

        try:
            username = validate_username(raw_username)
            if User.objects.filter(username__iexact=username).exists():
                raise ValidationError('Username is already taken.')
            temp_user = User(username=username, email=email)
            validate_password(password, user=temp_user)
        except ValidationError as exc:
            error = ' '.join(exc.messages)
        else:
            user = User.objects.create_user(username=username, email=email, password=password)
            user_settings = bootstrap_user_defaults(user)
            selected_curr = Currency.objects.filter(user=user, code=main_currency_code).first()
            if selected_curr:
                user_settings.main_currency = selected_curr
                user_settings.save(update_fields=['main_currency'])
            login(request, user)
            log_security_event('user_registered', request=request, user=user)
            return redirect('dashboard')

    return render(request, 'auth/register.html', {'error': error})


@require_http_methods(['GET', 'POST'])
def logout_view(request):
    if request.user.is_authenticated:
        log_security_event('user_logout', request=request, user=request.user)
    logout(request)
    return redirect('login')


@login_required
def dashboard_view(request):
    ctx = _base_context(request, active_nav='dashboard')
    metrics = compute_dashboard_metrics(request.user)
    ctx.update(metrics)
    ctx['recommendations'] = AIRecommendation.objects.filter(user=request.user)[:4]
    return render(request, 'dashboard.html', ctx)


@login_required
def subscriptions_view(request):
    ctx = _base_context(request, active_nav='subscriptions')
    user_settings = ctx['user_settings']
    main_curr = ctx['main_currency']

    qs = Subscription.objects.filter(user=request.user).select_related(
        'currency', 'category', 'payer', 'payment_method'
    ).prefetch_related('payment_records')

    q = sanitize_text(request.GET.get('q', ''), max_length=100)
    category_id = sanitize_text(request.GET.get('category', ''), max_length=16)
    cycle = sanitize_text(request.GET.get('cycle', ''), max_length=8)
    status = sanitize_text(request.GET.get('status', ''), max_length=16)
    sort = sanitize_text(request.GET.get('sort', 'next_payment'), max_length=24)

    if q:
        qs = qs.filter(name__icontains=q)
    if category_id.isdigit():
        qs = qs.filter(category_id=int(category_id))
    if cycle.isdigit():
        qs = qs.filter(cycle=int(cycle))
    if status == 'active':
        qs = qs.filter(inactive=False)
    elif status == 'inactive':
        qs = qs.filter(inactive=True)
    elif status == 'manual':
        qs = qs.filter(auto_renew=False, inactive=False)
    elif user_settings.hide_disabled and not status:
        qs = qs.filter(inactive=False)

    subs = list(qs)
    for sub in subs:
        sub.converted_price = convert_amount(sub.price, sub.currency, main_curr)
        sub.formatted_price = format_money(sub.price, sub.currency)
        sub.formatted_converted_price = format_money(sub.converted_price, main_curr)
        sub.is_different_currency = bool(sub.currency and main_curr and sub.currency.id != main_curr.id)
        sub.monthly_equivalent = (sub.converted_price * sub.monthly_multiplier()).quantize(Decimal('0.01'))
        sub.formatted_monthly_equivalent = format_money(sub.monthly_equivalent, main_curr)

    if sort == 'price_desc':
        subs.sort(key=lambda s: s.converted_price, reverse=True)
    elif sort == 'price_asc':
        subs.sort(key=lambda s: s.converted_price)
    elif sort == 'name':
        subs.sort(key=lambda s: s.name.lower())
    else:
        subs.sort(key=lambda s: (s.inactive, s.next_payment or date.max, s.name.lower()))

    metrics = compute_dashboard_metrics(request.user)
    ctx.update({
        'subscriptions': subs,
        'filter_q': q,
        'filter_category': category_id,
        'filter_cycle': cycle,
        'filter_status': status,
        'filter_sort': sort,
        'formatted_monthly_total': metrics['formatted_monthly_total'],
        'formatted_yearly_projection': metrics['formatted_yearly_projection'],
        'active_count': metrics['active_count'],
    })
    return render(request, 'subscriptions.html', ctx)


@login_required
@require_POST
def subscription_save_view(request):
    sub_id = sanitize_text(request.POST.get('subscription_id', ''), max_length=16)
    if sub_id and sub_id.isdigit():
        sub = get_object_or_404(Subscription, id=int(sub_id), user=request.user)
    else:
        sub = Subscription(user=request.user)

    user_settings = bootstrap_user_defaults(request.user)
    name = sanitize_text(request.POST.get('name', ''), max_length=120)
    if not name:
        messages.error(request, 'Subscription name is required.')
        return redirect('subscriptions')

    try:
        sub.price = validate_positive_decimal(request.POST.get('price', '0.00'), field_name='Price', allow_zero=True)
        sub.url = sanitize_url(request.POST.get('url', ''), max_length=500)
        if 'logo_file' in request.FILES and request.FILES['logo_file']:
            sub.logo_url = store_uploaded_file(request.FILES['logo_file'], folder='logos')
        elif request.POST.get('logo_url', '').strip():
            sub.logo_url = sanitize_url(request.POST.get('logo_url', ''), max_length=500)
    except ValidationError as exc:
        messages.error(request, ' '.join(exc.messages))
        return redirect('subscriptions')

    sub.name = name
    currency_id = sanitize_text(request.POST.get('currency_id', ''), max_length=16)
    if currency_id.isdigit():
        sub.currency = Currency.objects.filter(id=int(currency_id), user=request.user).first()
    elif not sub.currency_id:
        sub.currency = user_settings.main_currency

    cycle_val = sanitize_text(request.POST.get('cycle', '3'), max_length=4)
    sub.cycle = int(cycle_val) if cycle_val.isdigit() and int(cycle_val) in (1, 2, 3, 4, 5) else Subscription.CYCLE_MONTHLY

    freq_val = sanitize_text(request.POST.get('frequency', '1'), max_length=4)
    sub.frequency = min(max(int(freq_val), 1), 365) if freq_val.isdigit() else 1

    sub.next_payment = _parse_date(request.POST.get('next_payment'), fallback=date.today())
    sub.cancellation_date = _parse_date(request.POST.get('cancellation_date'), fallback=None)

    cat_id = sanitize_text(request.POST.get('category_id', ''), max_length=16)
    sub.category = Category.objects.filter(id=int(cat_id), user=request.user).first() if cat_id.isdigit() else None

    payer_id = sanitize_text(request.POST.get('payer_id', ''), max_length=16)
    sub.payer = HouseholdMember.objects.filter(id=int(payer_id), user=request.user).first() if payer_id.isdigit() else None

    pm_id = sanitize_text(request.POST.get('payment_method_id', ''), max_length=16)
    sub.payment_method = PaymentMethod.objects.filter(id=int(pm_id), user=request.user).first() if pm_id.isdigit() else None

    sub.auto_renew = request.POST.get('auto_renew') in ('on', 'true', '1')
    sub.inactive = request.POST.get('inactive') in ('on', 'true', '1')
    sub.notify = request.POST.get('notify') in ('on', 'true', '1')
    sub.notes = sanitize_text(request.POST.get('notes', ''), max_length=2000, allow_newlines=True)

    sub.save()
    return redirect('subscriptions')


@login_required
@require_POST
def subscription_renew_view(request, sub_id):
    sub = get_object_or_404(Subscription, id=sub_id, user=request.user)
    paid_date = sub.next_payment if sub.next_payment and sub.next_payment <= date.today() else date.today()
    SubscriptionPaymentRecord.objects.create(
        subscription=sub,
        paid_date=paid_date,
        amount=sub.price,
        currency_code=sub.currency.code if sub.currency else 'USD',
        notes='Renewal recorded',
    )
    ref_date = max(date.today(), sub.next_payment or date.today())
    sub.advance_next_payment(reference_date=ref_date)
    sub.save(update_fields=['next_payment', 'updated_at'])
    next_url = request.POST.get('next', '')
    if next_url in ('/', '/dashboard/', '/subscriptions/'):
        return redirect(next_url)
    return redirect('subscriptions')


@login_required
@require_POST
def subscription_delete_view(request, sub_id):
    sub = get_object_or_404(Subscription, id=sub_id, user=request.user)
    sub.delete()
    return redirect('subscriptions')


@login_required
@require_POST
def subscription_clone_view(request, sub_id):
    orig = get_object_or_404(Subscription, id=sub_id, user=request.user)
    cloned = Subscription.objects.create(
        user=request.user,
        name=f"{orig.name} (Copy)",
        price=orig.price,
        currency=orig.currency,
        cycle=orig.cycle,
        frequency=orig.frequency,
        next_payment=orig.next_payment,
        cancellation_date=orig.cancellation_date,
        auto_renew=orig.auto_renew,
        inactive=orig.inactive,
        notify=orig.notify,
        category=orig.category,
        payer=orig.payer,
        payment_method=orig.payment_method,
        logo_url=orig.logo_url,
        url=orig.url,
        notes=orig.notes,
    )
    cloned.categories.set(orig.categories.all())
    cloned.payers.set(orig.payers.all())
    messages.success(request, f'Cloned "{orig.name}".')
    return redirect('subscriptions')


@login_required
@require_POST
def subscriptions_bulk_action_view(request):
    raw_ids = request.POST.getlist('selected_ids')
    valid_ids = [int(i) for i in raw_ids if str(i).isdigit()]
    action = sanitize_text(request.POST.get('bulk_action', ''), max_length=32)

    if not valid_ids or not action:
        return redirect('subscriptions')

    qs = Subscription.objects.filter(user=request.user, id__in=valid_ids)
    if action == 'pause':
        qs.update(inactive=True)
        messages.success(request, f'Paused {qs.count()} subscriptions.')
    elif action == 'resume':
        qs.update(inactive=False)
        messages.success(request, f'Resumed {qs.count()} subscriptions.')
    elif action == 'enable_auto_renew':
        qs.update(auto_renew=True)
        messages.success(request, f'Enabled auto-renew on {qs.count()} subscriptions.')
    elif action == 'disable_auto_renew':
        qs.update(auto_renew=False)
        messages.success(request, f'Set {qs.count()} subscriptions to manual renewal.')
    elif action == 'delete':
        count = qs.count()
        qs.delete()
        messages.success(request, f'Deleted {count} subscriptions.')
    return redirect('subscriptions')


@login_required
def stats_view(request):
    ctx = _base_context(request, active_nav='stats')
    metrics = compute_dashboard_metrics(request.user)
    ctx.update(metrics)
    snapshots = list(YearlyCostSnapshot.objects.filter(user=request.user)[:12])
    if not snapshots and metrics['active_count'] > 0:
        snap = record_yearly_cost_snapshot(request.user)
        snapshots = [snap]
    for s in snapshots:
        s.formatted_monthly = format_money(s.total_monthly, metrics['main_currency'])
        s.formatted_yearly = format_money(s.total_yearly, metrics['main_currency'])
    ctx['cost_snapshots'] = snapshots
    return render(request, 'stats.html', ctx)


@login_required
def wishlist_view(request):
    ctx = _base_context(request, active_nav='wishlist')
    main_curr = ctx['main_currency']

    tab = sanitize_text(request.GET.get('tab', 'active'), max_length=16)
    q = sanitize_text(request.GET.get('q', ''), max_length=100)
    category_id = sanitize_text(request.GET.get('category', ''), max_length=16)
    priority = sanitize_text(request.GET.get('priority', ''), max_length=16)
    sort = sanitize_text(request.GET.get('sort', 'priority'), max_length=24)

    all_items = WishlistItem.objects.filter(user=request.user).select_related('currency', 'category')
    active_count = all_items.filter(is_purchased=False).count()
    purchased_count = all_items.filter(is_purchased=True).count()

    qs = all_items.filter(is_purchased=(tab == 'purchased'))
    if q:
        qs = qs.filter(name__icontains=q)
    if category_id.isdigit():
        qs = qs.filter(category_id=int(category_id))
    if priority in ('high', 'medium', 'low'):
        qs = qs.filter(priority=priority)

    items = list(qs)
    priority_rank = {'high': 0, 'medium': 1, 'low': 2}
    for item in items:
        item.converted_price = convert_amount(item.price, item.currency, main_curr)
        item.converted_saved = convert_amount(item.saved_amount, item.currency, main_curr)
        item.formatted_price = format_money(item.price, item.currency)
        item.formatted_saved = format_money(item.saved_amount, item.currency)
        item.formatted_converted_price = format_money(item.converted_price, main_curr)
        item.is_different_currency = bool(item.currency and main_curr and item.currency.id != main_curr.id)
        item.remaining_amount = max(item.price - item.saved_amount, Decimal('0.00'))
        item.formatted_remaining = format_money(item.remaining_amount, item.currency)

    if sort == 'price_desc':
        items.sort(key=lambda x: x.converted_price, reverse=True)
    elif sort == 'price_asc':
        items.sort(key=lambda x: x.converted_price)
    elif sort == 'date':
        items.sort(key=lambda x: x.created_at, reverse=True)
    else:
        items.sort(key=lambda x: (priority_rank.get(x.priority, 1), -x.converted_price))

    metrics = compute_dashboard_metrics(request.user)
    ctx.update({
        'wishlist_items': items,
        'tab': tab,
        'active_wishlist_count': active_count,
        'purchased_wishlist_count': purchased_count,
        'filter_q': q,
        'filter_category': category_id,
        'filter_priority': priority,
        'filter_sort': sort,
        'formatted_wishlist_total': metrics['formatted_wishlist_total'],
        'formatted_wishlist_saved_total': metrics['formatted_wishlist_saved_total'],
    })
    return render(request, 'wishlist.html', ctx)


@login_required
@require_POST
def wishlist_save_view(request):
    item_id = sanitize_text(request.POST.get('item_id', ''), max_length=16)
    if item_id and item_id.isdigit():
        item = get_object_or_404(WishlistItem, id=int(item_id), user=request.user)
    else:
        item = WishlistItem(user=request.user)

    user_settings = bootstrap_user_defaults(request.user)
    name = sanitize_text(request.POST.get('name', ''), max_length=150)
    if not name:
        messages.error(request, 'Item name is required.')
        return redirect('wishlist')

    try:
        item.price = validate_positive_decimal(request.POST.get('price', '0.00'), field_name='Price', allow_zero=True)
        item.saved_amount = validate_positive_decimal(request.POST.get('saved_amount', '0.00'), field_name='Saved amount', allow_zero=True)
        item.product_url = sanitize_url(request.POST.get('product_url', ''), max_length=600)
        if 'image_file' in request.FILES and request.FILES['image_file']:
            item.image_url = store_uploaded_file(request.FILES['image_file'], folder='wishlist')
        elif 'image_url' in request.POST:
            raw_img = request.POST.get('image_url', '').strip()
            if raw_img:
                item.image_url = sanitize_url(raw_img, max_length=600)
            elif not item.id:
                item.image_url = ''
    except ValidationError as exc:
        messages.error(request, ' '.join(exc.messages))
        return redirect('wishlist')

    item.name = name
    currency_id = sanitize_text(request.POST.get('currency_id', ''), max_length=16)
    if currency_id.isdigit():
        item.currency = Currency.objects.filter(id=int(currency_id), user=request.user).first()
    elif not item.currency_id:
        item.currency = user_settings.main_currency

    cat_id = sanitize_text(request.POST.get('category_id', ''), max_length=16)
    item.category = Category.objects.filter(id=int(cat_id), user=request.user).first() if cat_id.isdigit() else None

    priority = sanitize_text(request.POST.get('priority', 'medium'), max_length=16).lower()
    if priority in ('high', 'medium', 'low'):
        item.priority = priority

    item.target_date = _parse_date(request.POST.get('target_date'), fallback=None)
    item.notes = sanitize_text(request.POST.get('notes', ''), max_length=2000, allow_newlines=True)

    item.save()
    return redirect('wishlist')


@login_required
@require_POST
def wishlist_toggle_purchased_view(request, item_id):
    item = get_object_or_404(WishlistItem, id=item_id, user=request.user)
    item.is_purchased = not item.is_purchased
    item.purchased_at = date.today() if item.is_purchased else None
    if item.is_purchased and item.saved_amount < item.price:
        item.saved_amount = item.price
    item.save(update_fields=['is_purchased', 'purchased_at', 'saved_amount', 'updated_at'])
    return redirect('wishlist')


@login_required
@require_POST
def wishlist_delete_view(request, item_id):
    item = get_object_or_404(WishlistItem, id=item_id, user=request.user)
    item.delete()
    return redirect('wishlist')


@login_required
def calendar_view(request):
    ctx = _base_context(request, active_nav='calendar')
    main_curr = ctx['main_currency']

    today = date.today()
    try:
        year = min(max(int(request.GET.get('year', today.year)), 2000), 2100)
        month = int(request.GET.get('month', today.month))
    except ValueError:
        year, month = today.year, today.month

    if month < 1:
        month = 12
        year -= 1
    elif month > 12:
        month = 1
        year += 1

    prev_month = month - 1 if month > 1 else 12
    prev_year = year if month > 1 else year - 1
    next_month = month + 1 if month < 12 else 1
    next_year = year if month < 12 else year + 1

    cal = calendar.Calendar(firstweekday=6)
    month_days = list(cal.itermonthdates(year, month))

    subs = Subscription.objects.filter(user=request.user, inactive=False).select_related('currency')
    wishes = WishlistItem.objects.filter(
        user=request.user, is_purchased=False, target_date__isnull=False
    ).select_related('currency')

    events_by_date = {}
    monthly_due_total = Decimal('0.00')

    for sub in subs:
        if not sub.next_payment:
            continue
        occ = sub.next_payment
        conv = convert_amount(sub.price, sub.currency, main_curr)
        formatted = format_money(sub.price, sub.currency)
        if occ.year == year and occ.month == month:
            events_by_date.setdefault(occ, []).append({
                'name': sub.name,
                'price': formatted,
                'type': 'subscription',
            })
            monthly_due_total += conv

    for item in wishes:
        if item.target_date and item.target_date.year == year and item.target_date.month == month:
            events_by_date.setdefault(item.target_date, []).append({
                'name': f"Wishlist: {item.name}",
                'price': format_money(item.price, item.currency),
                'type': 'wishlist',
            })

    weeks = []
    for i in range(0, len(month_days), 7):
        week_row = []
        for d in month_days[i:i + 7]:
            week_row.append({
                'date': d,
                'day': d.day,
                'in_month': d.month == month,
                'is_today': d == today,
                'events': events_by_date.get(d, []),
            })
        weeks.append(week_row)

    metrics = compute_dashboard_metrics(request.user)
    ctx.update({
        'year': year,
        'month': month,
        'month_name': calendar.month_name[month],
        'prev_month': prev_month,
        'prev_year': prev_year,
        'next_month': next_month,
        'next_year': next_year,
        'weeks': weeks,
        'formatted_monthly_due_total': format_money(monthly_due_total, main_curr),
        'category_breakdown': metrics['category_breakdown'],
    })
    return render(request, 'calendar.html', ctx)


@login_required
def export_ical_view(request):
    ical_content = generate_ical_feed(request.user)
    response = HttpResponse(ical_content, content_type='text/calendar; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="mykeep.ics"'
    return response


@login_required
def settings_view(request):
    ctx = _base_context(request, active_nav='settings')
    user_settings = ctx['user_settings']
    notif_config, _ = NotificationConfig.objects.get_or_create(user=request.user)
    ctx['notification_config'] = notif_config
    ctx['all_payment_methods'] = PaymentMethod.objects.filter(user=request.user)

    # Prepare pending 2FA secret if user is not yet enrolled
    if not user_settings.totp_enabled:
        pending_secret = request.session.get('pending_totp_secret')
        if not pending_secret:
            pending_secret = generate_totp_secret()
            request.session['pending_totp_secret'] = pending_secret
        ctx['pending_totp_secret'] = pending_secret
        ctx['pending_otpauth_uri'] = build_otpauth_uri(pending_secret, request.user.username)

    try:
        remaining_recovery = len(json.loads(user_settings.recovery_codes_hashes or '[]'))
    except json.JSONDecodeError:
        remaining_recovery = 0
    ctx['remaining_recovery_codes_count'] = remaining_recovery
    ctx['fresh_recovery_codes'] = request.session.pop('fresh_recovery_codes', None)

    return render(request, 'settings.html', ctx)


@login_required
@require_POST
def settings_totp_setup_view(request):
    user_settings = bootstrap_user_defaults(request.user)
    action = sanitize_text(request.POST.get('action', ''), max_length=32)

    if action == 'enable':
        pending_secret = request.session.get('pending_totp_secret', '')
        code = sanitize_text(request.POST.get('code', ''), max_length=16)
        if not pending_secret or not verify_totp_code(pending_secret, code):
            log_security_event('totp_enable_failed', request=request, user=request.user)
            messages.error(request, 'Invalid 6-digit authenticator code. Please check your authenticator app and try again.')
            return redirect('settings')

        plain_codes, hashed_codes = generate_recovery_codes(count=8)
        user_settings.totp_secret = pending_secret
        user_settings.totp_enabled = True
        user_settings.recovery_codes_hashes = json.dumps(hashed_codes)
        user_settings.save(update_fields=['totp_secret', 'totp_enabled', 'recovery_codes_hashes'])
        request.session.pop('pending_totp_secret', None)
        request.session['fresh_recovery_codes'] = plain_codes
        log_security_event('totp_enabled', request=request, user=request.user)
        messages.success(request, 'Two-factor authentication enabled. Save your recovery codes below.')
        return redirect('settings')

    if action == 'disable':
        current_password = request.POST.get('current_password', '')
        if not request.user.check_password(current_password):
            log_security_event('totp_disable_failed_bad_password', request=request, user=request.user)
            messages.error(request, 'Current password is required to disable 2FA.')
            return redirect('settings')

        user_settings.totp_enabled = False
        user_settings.totp_secret = ''
        user_settings.recovery_codes_hashes = '[]'
        user_settings.save(update_fields=['totp_enabled', 'totp_secret', 'recovery_codes_hashes'])
        log_security_event('totp_disabled', request=request, user=request.user)
        messages.success(request, 'Two-factor authentication has been disabled.')
        return redirect('settings')

    if action == 'regenerate_recovery':
        current_password = request.POST.get('current_password', '')
        if not request.user.check_password(current_password):
            log_security_event('recovery_regen_failed_bad_password', request=request, user=request.user)
            messages.error(request, 'Current password is required to regenerate recovery codes.')
            return redirect('settings')

        if user_settings.totp_enabled:
            plain_codes, hashed_codes = generate_recovery_codes(count=8)
            user_settings.recovery_codes_hashes = json.dumps(hashed_codes)
            user_settings.save(update_fields=['recovery_codes_hashes'])
            request.session['fresh_recovery_codes'] = plain_codes
            log_security_event('recovery_codes_regenerated', request=request, user=request.user)
            messages.success(request, 'New recovery codes generated. Previous recovery codes are now invalid.')

    return redirect('settings')


@login_required
@require_POST
def settings_change_password_view(request):
    current_password = request.POST.get('current_password', '')
    new_password = request.POST.get('new_password', '')
    if not request.user.check_password(current_password):
        log_security_event('password_change_failed_wrong_current', request=request, user=request.user)
        messages.error(request, 'Current password is incorrect.')
        return redirect('settings')

    try:
        validate_password(new_password, user=request.user)
    except ValidationError as exc:
        messages.error(request, ' '.join(exc.messages))
        return redirect('settings')

    request.user.set_password(new_password)
    request.user.save()
    update_session_auth_hash(request, request.user)
    log_security_event('password_changed', request=request, user=request.user)
    messages.success(request, 'Password updated.')
    return redirect('settings')


@login_required
@require_POST
def settings_save_general_view(request):
    user_settings = bootstrap_user_defaults(request.user)
    main_curr_id = sanitize_text(request.POST.get('main_currency_id', ''), max_length=16)
    if main_curr_id.isdigit():
        curr = Currency.objects.filter(id=int(main_curr_id), user=request.user).first()
        if curr:
            user_settings.main_currency = curr

    try:
        user_settings.monthly_budget = validate_positive_decimal(
            request.POST.get('monthly_budget', '0.00'),
            field_name='Monthly budget',
            allow_zero=True,
        )
    except ValidationError as exc:
        messages.error(request, ' '.join(exc.messages))
        return redirect('settings')

    theme = sanitize_text(request.POST.get('theme', 'light'), max_length=16).lower()
    if theme in ('light', 'dark', 'system'):
        user_settings.theme = theme

    provider = sanitize_text(request.POST.get('exchange_provider', 'frankfurter'), max_length=24).lower()
    if provider in ('frankfurter', 'fixer'):
        user_settings.exchange_provider = provider
    user_settings.fixer_api_key = sanitize_text(request.POST.get('fixer_api_key', ''), max_length=128)
    user_settings.show_monthly_price = request.POST.get('show_monthly_price') in ('on', 'true', '1')
    user_settings.show_original_price = request.POST.get('show_original_price') in ('on', 'true', '1')
    user_settings.hide_disabled = request.POST.get('hide_disabled') in ('on', 'true', '1')
    user_settings.save()
    messages.success(request, 'General settings saved.')
    return redirect('settings')


@login_required
@require_POST
def theme_toggle_view(request):
    user_settings = bootstrap_user_defaults(request.user)
    user_settings.theme = 'dark' if user_settings.theme != 'dark' else 'light'
    user_settings.save(update_fields=['theme'])
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'theme': user_settings.theme})
    return redirect('dashboard')


@login_required
@require_POST
def settings_manage_taxonomy_view(request):
    action = sanitize_text(request.POST.get('action', ''), max_length=32)
    if action == 'add_category':
        name = sanitize_text(request.POST.get('name', ''), max_length=80)
        if name:
            Category.objects.get_or_create(user=request.user, name=name)
    elif action == 'delete_category':
        cid = sanitize_text(request.POST.get('id', ''), max_length=16)
        if cid.isdigit():
            Category.objects.filter(id=int(cid), user=request.user).delete()
    elif action == 'add_currency':
        name = sanitize_text(request.POST.get('name', ''), max_length=64)
        code = sanitize_text(request.POST.get('code', ''), max_length=10).upper()
        symbol = sanitize_text(request.POST.get('symbol', '$'), max_length=10)
        try:
            rate = validate_positive_decimal(request.POST.get('rate', '1.0'), field_name='Rate', allow_zero=False)
        except ValidationError:
            rate = Decimal('1.0')
        if name and CURRENCY_CODE_RE.match(code):
            Currency.objects.update_or_create(
                user=request.user,
                code=code,
                defaults={'name': name, 'symbol': symbol, 'rate': rate},
            )
    elif action == 'delete_currency':
        cid = sanitize_text(request.POST.get('id', ''), max_length=16)
        if cid.isdigit():
            Currency.objects.filter(id=int(cid), user=request.user).delete()
    elif action == 'add_household':
        name = sanitize_text(request.POST.get('name', ''), max_length=80)
        email = sanitize_text(request.POST.get('email', ''), max_length=150)
        if name:
            HouseholdMember.objects.create(user=request.user, name=name, email=email)
    elif action == 'delete_household':
        hid = sanitize_text(request.POST.get('id', ''), max_length=16)
        if hid.isdigit():
            HouseholdMember.objects.filter(id=int(hid), user=request.user).delete()
    elif action == 'add_payment_method':
        name = sanitize_text(request.POST.get('name', ''), max_length=80)
        if name:
            PaymentMethod.objects.get_or_create(user=request.user, name=name)
    elif action == 'delete_payment_method':
        pid = sanitize_text(request.POST.get('id', ''), max_length=16)
        if pid.isdigit():
            PaymentMethod.objects.filter(id=int(pid), user=request.user).delete()
    elif action == 'refresh_rates':
        try:
            update_exchange_rates_for_user(request.user)
            messages.success(request, 'Exchange rates updated.')
        except Exception as exc:
            messages.error(request, f'Could not refresh exchange rates: {exc}')
    elif action == 'generate_recommendations':
        generate_local_recommendations(request.user)
        messages.success(request, 'Recommendations updated.')
    elif action == 'regenerate_api_key':
        user_settings = bootstrap_user_defaults(request.user)
        user_settings.regenerate_api_key()
        log_security_event('api_key_regenerated', request=request, user=request.user)
        messages.success(request, 'API key regenerated.')
    return redirect('settings')


@login_required
@require_POST
def settings_save_notifications_view(request):
    cfg, _ = NotificationConfig.objects.get_or_create(user=request.user)
    days_val = sanitize_text(request.POST.get('days_before', '3'), max_length=4)
    cfg.days_before = min(max(int(days_val), 1), 60) if days_val.isdigit() else 3
    cfg.email_enabled = request.POST.get('email_enabled') in ('on', 'true', '1')
    cfg.smtp_host = sanitize_text(request.POST.get('smtp_host', ''), max_length=255)
    port_val = sanitize_text(request.POST.get('smtp_port', '587'), max_length=6)
    cfg.smtp_port = min(max(int(port_val), 1), 65535) if port_val.isdigit() else 587
    cfg.smtp_username = sanitize_text(request.POST.get('smtp_username', ''), max_length=255)
    if request.POST.get('smtp_password', '').strip():
        cfg.smtp_password = request.POST.get('smtp_password', '').strip()[:255]
    cfg.smtp_from_email = sanitize_text(request.POST.get('smtp_from_email', ''), max_length=254)
    cfg.discord_enabled = request.POST.get('discord_enabled') in ('on', 'true', '1')
    cfg.telegram_enabled = request.POST.get('telegram_enabled') in ('on', 'true', '1')
    cfg.telegram_bot_token = sanitize_text(request.POST.get('telegram_bot_token', ''), max_length=255)
    cfg.telegram_chat_id = sanitize_text(request.POST.get('telegram_chat_id', ''), max_length=128)
    cfg.gotify_enabled = request.POST.get('gotify_enabled') in ('on', 'true', '1')
    cfg.gotify_token = sanitize_text(request.POST.get('gotify_token', ''), max_length=255)
    cfg.pushover_enabled = request.POST.get('pushover_enabled') in ('on', 'true', '1')
    cfg.pushover_user_key = sanitize_text(request.POST.get('pushover_user_key', ''), max_length=255)
    cfg.pushover_api_token = sanitize_text(request.POST.get('pushover_api_token', ''), max_length=255)
    cfg.ntfy_enabled = request.POST.get('ntfy_enabled') in ('on', 'true', '1')
    cfg.ntfy_topic = sanitize_text(request.POST.get('ntfy_topic', ''), max_length=128)
    cfg.webhook_enabled = request.POST.get('webhook_enabled') in ('on', 'true', '1')

    try:
        cfg.discord_webhook_url = sanitize_url(request.POST.get('discord_webhook_url', ''), max_length=500)
        cfg.gotify_url = sanitize_url(request.POST.get('gotify_url', ''), max_length=500)
        cfg.ntfy_url = sanitize_url(request.POST.get('ntfy_url', ''), max_length=500)
        cfg.webhook_url = sanitize_url(request.POST.get('webhook_url', ''), max_length=500)
    except ValidationError as exc:
        messages.error(request, ' '.join(exc.messages))
        return redirect('settings')

    cfg.save()
    if request.POST.get('send_test') == '1':
        sent = dispatch_notification(
            request.user,
            title='MyKeep Test Notification',
            message='Your MyKeep notification channels are configured and working.',
        )
        if sent:
            messages.success(request, f'Test notification sent via: {", ".join(sent)}.')
        else:
            messages.warning(request, 'Saved settings, but no enabled channel succeeded during test dispatch.')
    else:
        messages.success(request, 'Notification settings saved.')
    return redirect('settings')


@login_required
def export_data_view(request):
    fmt = sanitize_text(request.GET.get('format', 'json'), max_length=10).lower()
    subs = Subscription.objects.filter(user=request.user).select_related(
        'currency', 'category', 'payer', 'payment_method'
    )
    wishes = WishlistItem.objects.filter(user=request.user).select_related('currency', 'category')

    if fmt == 'csv':
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([
            'Type', 'Name', 'Price', 'Currency', 'Cycle', 'Frequency',
            'Next Payment / Target Date', 'Category', 'Payment Method', 'Payer', 'URL', 'Notes',
        ])
        for s in subs:
            writer.writerow([
                'Subscription',
                s.name,
                str(s.price),
                s.currency.code if s.currency else 'USD',
                s.get_cycle_display(),
                s.frequency,
                s.next_payment.isoformat() if s.next_payment else '',
                s.category.name if s.category else '',
                s.payment_method.name if s.payment_method else '',
                s.payer.name if s.payer else '',
                s.url,
                s.notes,
            ])
        for w in wishes:
            writer.writerow([
                'Wishlist',
                w.name,
                str(w.price),
                w.currency.code if w.currency else 'USD',
                w.priority,
                1,
                w.target_date.isoformat() if w.target_date else '',
                w.category.name if w.category else '',
                '',
                '',
                w.product_url,
                w.notes,
            ])
        resp = HttpResponse(buf.getvalue(), content_type='text/csv; charset=utf-8')
        resp['Content-Disposition'] = 'attachment; filename="mykeep_export.csv"'
        return resp

    payload = {
        'subscriptions': [
            {
                'name': s.name,
                'price': str(s.price),
                'currency': s.currency.code if s.currency else 'USD',
                'cycle': s.cycle,
                'frequency': s.frequency,
                'next_payment': s.next_payment.isoformat() if s.next_payment else None,
                'auto_renew': s.auto_renew,
                'inactive': s.inactive,
                'category': s.category.name if s.category else None,
                'payer': s.payer.name if s.payer else None,
                'payment_method': s.payment_method.name if s.payment_method else None,
                'logo_url': s.logo_url,
                'url': s.url,
                'notes': s.notes,
            }
            for s in subs
        ],
        'wishlist': [
            {
                'name': w.name,
                'price': str(w.price),
                'saved_amount': str(w.saved_amount),
                'currency': w.currency.code if w.currency else 'USD',
                'category': w.category.name if w.category else None,
                'priority': w.priority,
                'image_url': w.image_url,
                'product_url': w.product_url,
                'target_date': w.target_date.isoformat() if w.target_date else None,
                'is_purchased': w.is_purchased,
                'notes': w.notes,
            }
            for w in wishes
        ],
    }
    resp = HttpResponse(json.dumps(payload, indent=2), content_type='application/json')
    resp['Content-Disposition'] = 'attachment; filename="mykeep_export.json"'
    return resp


@login_required
@require_POST
def import_data_view(request):
    upload = request.FILES.get('import_file')
    if not upload:
        messages.error(request, 'Please select a JSON or CSV file to import.')
        return redirect('settings')
    if upload.size > 5 * 1024 * 1024:
        messages.error(request, 'Import file exceeds 5 MB limit.')
        return redirect('settings')

    user_settings = bootstrap_user_defaults(request.user)
    default_curr = user_settings.main_currency
    raw_text = upload.read().decode('utf-8', errors='replace')
    imported_count = 0

    try:
        data = json.loads(raw_text)
        sub_list = data if isinstance(data, list) else data.get('subscriptions', [])
        wish_list = [] if isinstance(data, list) else data.get('wishlist', [])

        for row in sub_list[:500]:
            name = sanitize_text(row.get('name') or row.get('Name') or '', max_length=120)
            if not name:
                continue
            price_raw = str(row.get('price') or row.get('Price') or '0').replace('$', '').replace('€', '').replace('£', '').strip()
            cat_name = sanitize_text(row.get('category') or row.get('category_name') or '', max_length=80)
            cat = Category.objects.get_or_create(user=request.user, name=cat_name)[0] if cat_name else None
            curr_code = sanitize_text(row.get('currency') or row.get('currency_code') or '', max_length=10).upper()
            curr = Currency.objects.filter(user=request.user, code=curr_code).first() or default_curr

            try:
                price_val = validate_positive_decimal(price_raw, allow_zero=True)
            except ValidationError:
                price_val = Decimal('0.00')

            Subscription.objects.create(
                user=request.user,
                name=name,
                price=price_val,
                currency=curr,
                cycle=int(row.get('cycle', 3)) if str(row.get('cycle', '')).isdigit() else 3,
                frequency=int(row.get('frequency', 1)) if str(row.get('frequency', '')).isdigit() else 1,
                next_payment=_parse_date(row.get('next_payment'), fallback=date.today()),
                category=cat,
                notes=sanitize_text(row.get('notes') or '', max_length=2000, allow_newlines=True),
            )
            imported_count += 1

        for w in wish_list[:500]:
            name = sanitize_text(w.get('name') or '', max_length=150)
            if not name:
                continue
            try:
                price_val = validate_positive_decimal(w.get('price', '0'), allow_zero=True)
                saved_val = validate_positive_decimal(w.get('saved_amount', '0'), allow_zero=True)
            except ValidationError:
                price_val, saved_val = Decimal('0.00'), Decimal('0.00')

            WishlistItem.objects.create(
                user=request.user,
                name=name,
                price=price_val,
                saved_amount=saved_val,
                currency=default_curr,
                priority=w.get('priority', 'medium') if w.get('priority') in ('high', 'medium', 'low') else 'medium',
                notes=sanitize_text(w.get('notes', ''), max_length=2000, allow_newlines=True),
            )
            imported_count += 1
    except json.JSONDecodeError:
        reader = csv.DictReader(io.StringIO(raw_text))
        for idx, row in enumerate(reader):
            if idx >= 500:
                break
            name = sanitize_text(row.get('Name') or row.get('name') or '', max_length=120)
            if not name:
                continue
            row_type = sanitize_text(row.get('Type') or 'Subscription', max_length=32).lower()
            price_raw = (row.get('Price') or row.get('price') or '0').replace('$', '').replace('€', '').strip()
            try:
                price_val = validate_positive_decimal(price_raw, allow_zero=True)
            except ValidationError:
                price_val = Decimal('0.00')

            if row_type == 'wishlist':
                WishlistItem.objects.create(
                    user=request.user,
                    name=name,
                    price=price_val,
                    currency=default_curr,
                    notes=sanitize_text(row.get('Notes') or '', max_length=2000, allow_newlines=True),
                )
            else:
                Subscription.objects.create(
                    user=request.user,
                    name=name,
                    price=price_val,
                    currency=default_curr,
                    next_payment=_parse_date(row.get('Next Payment') or row.get('Next Payment / Target Date'), fallback=date.today()),
                    notes=sanitize_text(row.get('Notes') or '', max_length=2000, allow_newlines=True),
                )
            imported_count += 1

    log_security_event('data_imported', request=request, user=request.user, details=f'count={imported_count}')
    messages.success(request, f'Imported {imported_count} items.')
    return redirect('settings')


def api_summary_view(request):
    ip = get_client_ip(request)
    allowed, _ = check_rate_limit('api_summary', ip, limit=30, window_seconds=60)
    if not allowed:
        log_security_event('api_rate_limited', request=request)
        return JsonResponse({'error': 'Rate limit exceeded'}, status=429)

    api_key = sanitize_text(request.GET.get('api_key') or request.headers.get('X-API-Key') or '', max_length=64)
    user = request.user if request.user.is_authenticated else None
    if not user and api_key:
        us = UserSettings.objects.filter(api_key=api_key).select_related('user').first()
        if us:
            user = us.user
    if not user:
        log_security_event('api_unauthorized', request=request)
        return JsonResponse({'error': 'Unauthorized'}, status=401)

    metrics = compute_dashboard_metrics(user)
    return JsonResponse({
        'monthly_total': str(metrics['monthly_total']),
        'yearly_projection': str(metrics['yearly_projection']),
        'active_subscriptions': metrics['active_count'],
        'due_next_7_days': str(metrics['due_next_7_days']),
        'wishlist_total': str(metrics['wishlist_total']),
        'wishlist_count': metrics['wishlist_count'],
    })
