import os
from pathlib import Path
from django.db.backends.signals import connection_created
from django.dispatch import receiver

BASE_DIR = Path(__file__).resolve().parent.parent

# Load simple key=value pairs from .env if present
env_path = BASE_DIR / '.env'
if env_path.exists():
    for raw_line in env_path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if line and not line.startswith('#') and '=' in line:
            key, val = line.split('=', 1)
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))

SECRET_KEY = os.environ.get(
    'SECRET_KEY',
    'django-insecure-9(pty%&$-60u2kx9htxzc((vt6y&_t&%adt9#r*by++*k^^!)e',
)

DEBUG = os.environ.get('DEBUG', 'True').lower() in ('1', 'true', 'yes')

ALLOWED_HOSTS = [
    h.strip()
    for h in os.environ.get('ALLOWED_HOSTS', '*').split(',')
    if h.strip()
]

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'app',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'app.middleware.SecurityHeadersAndRateLimitMiddleware',
]

ROOT_URLCONF = 'core.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'core.wsgi.application'

# Single-file SQLite database stored in ./data/mykeep.db for easy single-folder Docker volume mounting
DATA_DIR = BASE_DIR / 'data'
DATA_DIR.mkdir(parents=True, exist_ok=True)

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': DATA_DIR / 'mykeep.db',
        'OPTIONS': {
            'timeout': 20,
        },
    }
}


@receiver(connection_created)
def configure_sqlite_wal(sender, connection, **kwargs):
    # Enable SQLite WAL journal mode, busy timeout, and ensure new columns exist seamlessly
    if connection.vendor == 'sqlite':
        with connection.cursor() as cursor:
            cursor.execute('PRAGMA journal_mode=WAL;')
            cursor.execute('PRAGMA synchronous=NORMAL;')
            cursor.execute('PRAGMA busy_timeout=5000;')
            cursor.execute('PRAGMA foreign_keys=ON;')
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='app_usersettings'")
            if cursor.fetchone():
                cursor.execute('PRAGMA table_info(app_usersettings)')
                existing_cols = {row[1] for row in cursor.fetchall()}
                extra_cols = [
                    ('totp_enabled', "BOOLEAN NOT NULL DEFAULT 0"),
                    ('totp_secret', " VARCHAR(64) NOT NULL DEFAULT ''"),
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


# Zero-dependency in-process memory cache
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'mykeep-cache',
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/login/'

# Session, CSRF, Upload, and Logging Security Hardening
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_AGE = 60 * 60 * 24 * 14
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_SECURE = not DEBUG
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'
DATA_UPLOAD_MAX_MEMORY_SIZE = 6 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 6 * 1024 * 1024

LOG_DIR = BASE_DIR / 'data' / 'logs'
LOG_DIR.mkdir(parents=True, exist_ok=True)

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'security': {
            'format': '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'security',
        },
        'security_file': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': LOG_DIR / 'security.log',
            'maxBytes': 5 * 1024 * 1024,
            'backupCount': 3,
            'formatter': 'security',
        },
    },
    'loggers': {
        'mykeep.security': {
            'handlers': ['console', 'security_file'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
