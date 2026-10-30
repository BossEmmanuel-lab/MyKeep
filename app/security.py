import base64
import hashlib
import hmac
import html
import io
import logging
import re
import secrets
import struct
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import quote, urlparse

from django.contrib.auth.hashers import check_password, make_password
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from PIL import Image

security_logger = logging.getLogger('mykeep.security')

CONTROL_CHARS_RE = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')
USERNAME_RE = re.compile(r'^[a-zA-Z0-9_.-]{3,32}$')
CURRENCY_CODE_RE = re.compile(r'^[A-Z]{2,6}$')
SAFE_SCHEMES = ('http', 'https')
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
ALLOWED_IMAGE_FORMATS = {'PNG': '.png', 'JPEG': '.jpg', 'WEBP': '.webp', 'GIF': '.gif'}


def log_security_event(event_type, request=None, user=None, details=''):
    # Structured security audit logging
    ip = get_client_ip(request) if request else 'system'
    uname = getattr(user, 'username', None) or (
        request.user.username if request and getattr(request, 'user', None) and request.user.is_authenticated else 'anonymous'
    )
    security_logger.info(
        'event=%s ip=%s user=%s details=%s',
        event_type,
        ip,
        uname,
        sanitize_text(str(details), max_length=300),
    )


def get_client_ip(request):
    # Extract client IP safely
    if not request:
        return '0.0.0.0'
    remote_addr = request.META.get('REMOTE_ADDR', '0.0.0.0')
    return remote_addr.strip()[:45]


def check_rate_limit(scope, identifier, limit=5, window_seconds=300):
    # Sliding-window style counter rate limiter using LocMemCache
    key = f"rl:{scope}:{identifier}"
    current = cache.get(key, 0)
    if current >= limit:
        return False, 0
    if current == 0:
        cache.set(key, 1, timeout=window_seconds)
    else:
        try:
            cache.incr(key)
        except ValueError:
            cache.set(key, current + 1, timeout=window_seconds)
    return True, max(limit - (current + 1), 0)


def reset_rate_limit(scope, identifier):
    cache.delete(f"rl:{scope}:{identifier}")


def rate_limit_exceeded_response(message='Too many requests. Please wait and try again.'):
    return HttpResponse(message, status=429, content_type='text/plain; charset=utf-8')


def sanitize_text(value, max_length=255, allow_newlines=False):
    # Strip control characters, HTML tags, and clamp length
    if value is None:
        return ''
    text = str(value)
    text = CONTROL_CHARS_RE.sub('', text)
    if not allow_newlines:
        text = text.replace('\r', ' ').replace('\n', ' ')
    # Strip any HTML/script tags while preserving plain characters like & or "
    text = re.sub(r'<[^>]*>', '', text)
    return text.strip()[:max_length]


def validate_username(username):
    clean = sanitize_text(username, max_length=32)
    if not USERNAME_RE.match(clean):
        raise ValidationError('Username must be 3-32 characters and contain only letters, numbers, dots, hyphens, or underscores.')
    return clean


def sanitize_url(value, max_length=500):
    # Only allow http:// and https:// URLs or local /media/ paths; block javascript:, data:, file:
    if not value:
        return ''
    raw = sanitize_text(value, max_length=max_length)
    if raw.startswith('/media/'):
        if '..' in raw or '\\' in raw:
            raise ValidationError('Invalid media path.')
        return raw
    parsed = urlparse(raw)
    if parsed.scheme.lower() not in SAFE_SCHEMES or not parsed.netloc:
        raise ValidationError('URL must start with http:// or https://.')
    return raw


def validate_positive_decimal(value, field_name='Amount', max_value=Decimal('9999999.99'), allow_zero=True):
    try:
        dec = Decimal(str(value or '0').strip())
    except (InvalidOperation, ValueError):
        raise ValidationError(f'{field_name} must be a valid number.')
    if dec.is_nan() or dec.is_infinite():
        raise ValidationError(f'{field_name} must be a finite number.')
    if allow_zero and dec < Decimal('0.00'):
        raise ValidationError(f'{field_name} cannot be negative.')
    if not allow_zero and dec <= Decimal('0.00'):
        raise ValidationError(f'{field_name} must be greater than zero.')
    if dec > max_value:
        raise ValidationError(f'{field_name} exceeds maximum allowed value.')
    return dec.quantize(Decimal('0.01'))


def validate_and_sanitize_image(uploaded_file):
    # Verify file size, magic bytes via Pillow, strip EXIF metadata, and re-encode cleanly
    if uploaded_file.size > MAX_UPLOAD_BYTES:
        raise ValidationError('Image file exceeds 5 MB limit.')

    raw_bytes = uploaded_file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw_bytes) > MAX_UPLOAD_BYTES:
        raise ValidationError('Image file exceeds 5 MB limit.')

    try:
        with Image.open(io.BytesIO(raw_bytes)) as img:
            img.verify()
        with Image.open(io.BytesIO(raw_bytes)) as img:
            fmt = (img.format or '').upper()
            if fmt not in ALLOWED_IMAGE_FORMATS:
                raise ValidationError('Unsupported image format. Please upload PNG, JPEG, WEBP, or GIF.')
            if img.width > 4096 or img.height > 4096:
                img.thumbnail((2048, 2048))
            # Re-encode into clean buffer to strip EXIF and any embedded payloads
            out = io.BytesIO()
            save_fmt = 'PNG' if fmt == 'PNG' else ('WEBP' if fmt == 'WEBP' else 'JPEG')
            if save_fmt == 'JPEG' and img.mode not in ('RGB', 'L'):
                img = img.convert('RGB')
            img.save(out, format=save_fmt, quality=88)
            ext = '.png' if save_fmt == 'PNG' else ('.webp' if save_fmt == 'WEBP' else '.jpg')
            return out.getvalue(), ext
    except ValidationError:
        raise
    except Exception:
        raise ValidationError('Uploaded file is not a valid image.')


def generate_totp_secret():
    # Generate a 160-bit RFC 6238 base32 secret (32 base32 chars without padding)
    raw = secrets.token_bytes(20)
    return base64.b32encode(raw).decode('ascii').rstrip('=')


def _totp_at(secret_b32, counter, digits=6):
    # Compute RFC 4226 / RFC 6238 HOTP/TOTP value for a given counter
    cleaned = re.sub(r'[^A-Z2-7]', '', str(secret_b32 or '').upper())
    if not cleaned:
        return ''
    pad_len = (8 - (len(cleaned) % 8)) % 8
    key = base64.b32decode(cleaned + ('=' * pad_len), casefold=True)
    msg = struct.pack('>Q', int(counter))
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code_int = struct.unpack('>I', digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(code_int % (10 ** digits)).zfill(digits)


def get_current_totp_code(secret_b32, timestamp=None, step=30):
    ts = int(timestamp if timestamp is not None else time.time())
    return _totp_at(secret_b32, ts // step)


def verify_totp_code(secret_b32, user_code, window=1, step=30, timestamp=None):
    # Constant-time RFC 6238 TOTP check across [-window, +window] steps
    code = re.sub(r'\s+', '', str(user_code or ''))
    if not code.isdigit() or len(code) != 6 or not secret_b32:
        return False
    ts = int(timestamp if timestamp is not None else time.time())
    current_counter = ts // step
    for delta in range(-window, window + 1):
        candidate = _totp_at(secret_b32, current_counter + delta)
        if candidate and hmac.compare_digest(candidate, code):
            return True
    return False


def build_otpauth_uri(secret_b32, username, issuer='MyKeep'):
    label = quote(f"{issuer}:{username}")
    issuer_param = quote(issuer)
    return f"otpauth://totp/{label}?secret={secret_b32}&issuer={issuer_param}&algorithm=SHA1&digits=6&period=30"


def generate_recovery_codes(count=8):
    # Generate 8 single-use recovery codes (e.g. A1B2-C3D4) and their hashed storage representations
    alphabet = '23456789ABCDEFGHJKLMNPQRSTUVWXYZ'
    plain_codes = []
    hashed_codes = []
    for _ in range(count):
        part1 = ''.join(secrets.choice(alphabet) for _ in range(4))
        part2 = ''.join(secrets.choice(alphabet) for _ in range(4))
        code = f"{part1}-{part2}"
        plain_codes.append(code)
        hashed_codes.append(make_password(code))
    return plain_codes, hashed_codes


def verify_and_consume_recovery_code(hashed_list, submitted_code):
    # Check submitted recovery code against hashed list and remove it if matched
    normalized = str(submitted_code or '').strip().upper().replace(' ', '')
    if len(normalized) == 8 and '-' not in normalized:
        normalized = f"{normalized[:4]}-{normalized[4:]}"
    if not re.match(r'^[A-Z0-9]{4}-[A-Z0-9]{4}$', normalized):
        return False, hashed_list

    remaining = []
    matched = False
    for hashed in hashed_list:
        if not matched and check_password(normalized, hashed):
            matched = True
        else:
            remaining.append(hashed)
    return matched, remaining
