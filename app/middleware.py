from django.http import HttpResponse
from .security import check_rate_limit, get_client_ip, log_security_event


class SecurityHeadersAndRateLimitMiddleware:
    # Apply strict security headers and global request rate limiting on every request

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        ip = get_client_ip(request)

        # Global write rate limit: max 60 POST requests per 60 seconds per IP
        if request.method == 'POST':
            allowed, _ = check_rate_limit('global_post', ip, limit=60, window_seconds=60)
            if not allowed:
                log_security_event('rate_limit_exceeded_post', request=request, details=request.path)
                return HttpResponse(
                    'Too many requests. Please slow down and try again shortly.',
                    status=429,
                    content_type='text/plain; charset=utf-8',
                )

        response = self.get_response(request)

        # Apply defense-in-depth HTTP security headers
        response.setdefault('X-Content-Type-Options', 'nosniff')
        response.setdefault('X-Frame-Options', 'DENY')
        response.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
        response.setdefault(
            'Permissions-Policy',
            'accelerometer=(), camera=(), geolocation=(), gyroscope=(), microphone=(), payment=(), usb=()',
        )
        response.setdefault(
            'Content-Security-Policy',
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data: https: http:; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self';",
        )
        return response
