from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse

from .models import Currency, Subscription, WishlistItem
from .services import bootstrap_user_defaults, compute_dashboard_metrics, convert_amount, is_safe_public_url


class MyKeepCoreTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username='alex', email='alex@example.com', password='secret123')
        self.settings = bootstrap_user_defaults(self.user)
        self.client.login(username='alex', password='secret123')

    def test_currency_conversion_and_dashboard_metrics(self):
        usd = Currency.objects.get(user=self.user, code='USD')
        eur = Currency.objects.get(user=self.user, code='EUR')
        eur.rate = Decimal('0.800000')
        eur.save()

        Subscription.objects.create(
            user=self.user,
            name='Netflix',
            price=Decimal('15.99'),
            currency=usd,
            cycle=Subscription.CYCLE_MONTHLY,
            frequency=1,
            next_payment=date.today() + timedelta(days=3),
        )
        Subscription.objects.create(
            user=self.user,
            name='Hetzner Cloud',
            price=Decimal('8.00'),
            currency=eur,
            cycle=Subscription.CYCLE_MONTHLY,
            frequency=1,
            next_payment=date.today() + timedelta(days=5),
        )
        WishlistItem.objects.create(
            user=self.user,
            name='MacBook Pro 14"',
            price=Decimal('1999.00'),
            saved_amount=Decimal('500.00'),
            currency=usd,
            priority='high',
            image_url='https://example.com/macbook.png',
            product_url='https://www.apple.com/macbook-pro/',
        )

        metrics = compute_dashboard_metrics(self.user)
        # 15.99 USD + (8.00 EUR / 0.80 = 10.00 USD) = 25.99 USD
        self.assertEqual(metrics['monthly_total'], Decimal('25.99'))
        self.assertEqual(metrics['active_count'], 2)
        self.assertEqual(metrics['wishlist_total'], Decimal('1999.00'))

    def test_wishlist_image_url_and_file_upload(self):
        # Create via image link URL
        resp = self.client.post(reverse('wishlist_save'), {
            'name': 'MacBook Pro M4',
            'price': '2499.00',
            'saved_amount': '1000.00',
            'priority': 'high',
            'image_url': 'https://images.example.com/mbp.jpg',
            'product_url': 'https://apple.com/macbook-pro',
        })
        self.assertEqual(resp.status_code, 302)
        item = WishlistItem.objects.get(user=self.user, name='MacBook Pro M4')
        self.assertEqual(item.image_url, 'https://images.example.com/mbp.jpg')
        self.assertEqual(item.domain, 'apple.com')

        # Create via uploaded image file
        png_bytes = (
            b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
            b'\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0'
            b'\x00\x00\x03\x01\x01\x00\x18\xdd\x8d\xb0\x00\x00\x00\x00IEND\xaeB`\x82'
        )
        upload = SimpleUploadedFile('macbook.png', png_bytes, content_type='image/png')
        resp2 = self.client.post(reverse('wishlist_save'), {
            'name': 'Studio Display',
            'price': '1599.00',
            'priority': 'medium',
            'image_file': upload,
        })
        self.assertEqual(resp2.status_code, 302)
        item2 = WishlistItem.objects.get(user=self.user, name='Studio Display')
        self.assertTrue(item2.image_url.endswith('.png'))

    def test_subscription_renewal_records_payment_and_advances_date(self):
        sub = Subscription.objects.create(
            user=self.user,
            name='Spotify',
            price=Decimal('10.99'),
            currency=self.settings.main_currency,
            cycle=Subscription.CYCLE_MONTHLY,
            frequency=1,
            next_payment=date(2026, 10, 1),
            auto_renew=False,
        )
        resp = self.client.post(reverse('subscription_renew', args=[sub.id]))
        self.assertEqual(resp.status_code, 302)
        sub.refresh_from_db()
        self.assertGreater(sub.next_payment, date(2026, 10, 1))
        self.assertEqual(sub.payment_records.count(), 1)

    def test_ssrf_guard_blocks_loopback_and_private_ips(self):
        self.assertFalse(is_safe_public_url('http://127.0.0.1:8000/admin'))
        self.assertFalse(is_safe_public_url('http://169.254.169.254/latest/meta-data/'))

    def test_pages_render_and_api_summary_works(self):
        for route in ('dashboard', 'subscriptions', 'wishlist', 'calendar', 'stats', 'settings', 'export_ical'):
            response = self.client.get(reverse(route))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response['X-Frame-Options'], 'DENY')
            self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
            self.assertIn("default-src 'self'", response['Content-Security-Policy'])

        api_resp = self.client.get(reverse('api_summary') + f'?api_key={self.settings.api_key}')
        self.assertEqual(api_resp.status_code, 200)
        self.assertIn('monthly_total', api_resp.json())

    def test_subscription_clone_and_bulk_actions(self):
        sub = Subscription.objects.create(
            user=self.user,
            name='GitHub Copilot',
            price=Decimal('10.00'),
            currency=self.settings.main_currency,
            cycle=Subscription.CYCLE_MONTHLY,
            frequency=1,
            next_payment=date.today(),
        )
        # Clone subscription
        clone_resp = self.client.post(reverse('subscription_clone', args=[sub.id]))
        self.assertEqual(clone_resp.status_code, 302)
        cloned = Subscription.objects.filter(user=self.user, name='GitHub Copilot (Copy)').first()
        self.assertIsNotNone(cloned)

        # Bulk pause both subscriptions
        bulk_resp = self.client.post(reverse('subscriptions_bulk_action'), {
            'bulk_action': 'pause',
            'selected_ids': [str(sub.id), str(cloned.id)],
        })
        self.assertEqual(bulk_resp.status_code, 302)
        sub.refresh_from_db()
        cloned.refresh_from_db()
        self.assertTrue(sub.inactive)
        self.assertTrue(cloned.inactive)

    def test_totp_2fa_enrollment_login_and_recovery_codes(self):
        from .security import get_current_totp_code

        # Visit settings to initialize pending TOTP secret
        resp = self.client.get(reverse('settings'))
        self.assertEqual(resp.status_code, 200)
        pending_secret = self.client.session.get('pending_totp_secret')
        self.assertTrue(pending_secret)

        # Enable 2FA with valid TOTP code
        valid_code = get_current_totp_code(pending_secret)
        enable_resp = self.client.post(reverse('settings_totp_setup'), {
            'action': 'enable',
            'code': valid_code,
        })
        self.assertEqual(enable_resp.status_code, 302)
        self.settings.refresh_from_db()
        self.assertTrue(self.settings.totp_enabled)
        recovery_codes = self.client.session.get('fresh_recovery_codes')
        self.assertEqual(len(recovery_codes), 8)

        # Log out and verify password login redirects to /login/2fa/
        self.client.post(reverse('logout'))
        login_resp = self.client.post(reverse('login'), {
            'username': 'alex',
            'password': 'secret123',
        })
        self.assertRedirects(login_resp, reverse('login_2fa'))

        # Verify TOTP code logs user in
        totp_resp = self.client.post(reverse('login_2fa'), {
            'code': get_current_totp_code(self.settings.totp_secret),
        })
        self.assertRedirects(totp_resp, reverse('dashboard'))

        # Log out again and sign in using a single-use recovery code
        self.client.post(reverse('logout'))
        self.client.post(reverse('login'), {'username': 'alex', 'password': 'secret123'})
        first_recovery = recovery_codes[0]
        rec_resp = self.client.post(reverse('login_2fa'), {'code': first_recovery})
        self.assertRedirects(rec_resp, reverse('dashboard'))

        # Verify the consumed recovery code cannot be reused
        self.client.post(reverse('logout'))
        self.client.post(reverse('login'), {'username': 'alex', 'password': 'secret123'})
        reuse_resp = self.client.post(reverse('login_2fa'), {'code': first_recovery})
        self.assertEqual(reuse_resp.status_code, 200)

    def test_input_sanitization_and_rate_limiting(self):
        from .security import check_rate_limit, reset_rate_limit, sanitize_text, sanitize_url
        from django.core.exceptions import ValidationError

        # HTML tags and control characters are stripped
        cleaned = sanitize_text('<script>alert(1)</script>Netflix\x00', max_length=50)
        self.assertEqual(cleaned, 'alert(1)Netflix')

        # Unsafe URLs (javascript:, data:) are rejected
        with self.assertRaises(ValidationError):
            sanitize_url('javascript:alert(1)')

        # Rate limiter blocks after threshold
        reset_rate_limit('test_scope', '127.0.0.1')
        for _ in range(3):
            ok, _ = check_rate_limit('test_scope', '127.0.0.1', limit=3, window_seconds=60)
            self.assertTrue(ok)
        blocked, _ = check_rate_limit('test_scope', '127.0.0.1', limit=3, window_seconds=60)
        self.assertFalse(blocked)
