"""Base settings shared by every ASHOS environment.

Rules that hold across all environments:
  * Configuration comes from the environment only (goal.txt D15). No secret,
    endpoint, or key is ever hard-coded here.
  * AI settings in this module are *bootstrap defaults*. Once AI Center holds
    rows in ``ai_center.ModelConfig`` the database wins (goal.txt D07).
  * Nothing here may import a heavy AI library. Startup must stay fast and must
    not require torch to be installed.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
env_file = BASE_DIR / ".env"
if env_file.exists():
    env.read_env(str(env_file))

# ==============================================================================
# Core
# ==============================================================================
SECRET_KEY = env("DJANGO_SECRET_KEY", default="insecure-dev-key-change-me")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])
CSRF_TRUSTED_ORIGINS = env.list("DJANGO_CSRF_TRUSTED_ORIGINS", default=[])

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "accounts.User"

# ==============================================================================
# Applications
# ==============================================================================
DJANGO_APPS = [
    "daphne",  # must precede staticfiles so runserver speaks ASGI
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "drf_spectacular",
    "django_filters",
    "corsheaders",
    "channels",
    "django_celery_beat",
    "django_celery_results",
]

# Domain apps. Order matters only for template/static override precedence.
LOCAL_APPS = [
    "apps.core",
    "apps.accounts",
    "apps.tenants",
    "apps.guests",
    "apps.rooms",
    "apps.booking",
    "apps.housekeeping",
    "apps.restaurant",
    "apps.billing",
    "apps.reception",
    "apps.ai_center",
    "apps.vision",
    "apps.rag",
    "apps.vector_search",
    "apps.notifications",
    "apps.dashboard",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

# ==============================================================================
# Middleware
# ==============================================================================
MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    # ASHOS: binds request-id + current tenant + actor for audit logging.
    "apps.core.middleware.RequestContextMiddleware",
    "apps.tenants.middleware.CurrentTenantMiddleware",
]

# ==============================================================================
# Templates
# ==============================================================================
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.ui_context",
            ],
        },
    },
]

# ==============================================================================
# Database — PostgreSQL + pgvector (goal.txt D04)
# ==============================================================================
DATABASES = {
    "default": env.db(
        "DATABASE_URL",
        default="postgres://ashos:ashos@localhost:5432/ashos",
    ),
}
DATABASES["default"]["CONN_MAX_AGE"] = env.int("DB_CONN_MAX_AGE", default=60)
DATABASES["default"]["ATOMIC_REQUESTS"] = False
DATABASES["default"].setdefault("OPTIONS", {})

# ==============================================================================
# Cache / Channels
# ==============================================================================
REDIS_URL = env("REDIS_URL", default="redis://localhost:6379/0")

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
        "KEY_PREFIX": "ashos",
    }
}

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {"hosts": [env("CHANNELS_REDIS_URL", default="redis://localhost:6379/3")]},
    }
}

# ==============================================================================
# Celery
# ==============================================================================
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="redis://localhost:6379/1")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default="django-db")
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = env("DJANGO_TIME_ZONE", default="Asia/Dhaka")
CELERY_TASK_TIME_LIMIT = 600
CELERY_TASK_SOFT_TIME_LIMIT = 540
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"

# ==============================================================================
# Auth / password policy
# ==============================================================================
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 10},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Order matters, and the omission matters more: plain ModelBackend is NOT
# listed. It would happily authenticate a locked-out account, silently undoing
# the brute-force protection in EmailBackend. EmailBackend subclasses it, so
# superuser and directly-granted permissions still resolve.
AUTHENTICATION_BACKENDS = [
    "apps.accounts.backends.EmailBackend",  # authentication + lockout + direct grants
    "apps.accounts.backends.RolePermissionBackend",  # per-hotel authorisation
]

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "dashboard:home"
LOGOUT_REDIRECT_URL = "accounts:login"

# ==============================================================================
# DRF
# ==============================================================================
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    "DEFAULT_PAGINATION_CLASS": "api.pagination.CursorPagination",  # goal.txt D18
    "PAGE_SIZE": 25,
    "DEFAULT_FILTER_BACKENDS": (
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ),
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "api.exceptions.problem_detail_handler",  # goal.txt D17
    "DEFAULT_THROTTLE_CLASSES": (
        "rest_framework.throttling.ScopedRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
        "rest_framework.throttling.AnonRateThrottle",
    ),
    "DEFAULT_THROTTLE_RATES": {
        "user": "1000/hour",
        "anon": "60/hour",
        "ai_chat": "60/minute",
        "ai_voice": "120/minute",
        "vision": "30/minute",
        "auth": "10/minute",
    },
    "DEFAULT_VERSIONING_CLASS": "rest_framework.versioning.URLPathVersioning",
    "DEFAULT_VERSION": "v1",
    "ALLOWED_VERSIONS": ["v1"],
    "TEST_REQUEST_DEFAULT_FORMAT": "json",
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=env.int("JWT_ACCESS_LIFETIME_MIN", default=15)),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=env.int("JWT_REFRESH_LIFETIME_DAYS", default=7)),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "ALGORITHM": "HS256",
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "ASHOS API",
    "DESCRIPTION": "AI Smart Hotel OS — AI-native hotel management platform.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "SCHEMA_PATH_PREFIX": "/api/v[0-9]",
    "COMPONENT_SPLIT_REQUEST": True,
    "SORT_OPERATIONS": False,
    "ENUM_NAME_OVERRIDES": {},
    "TAGS": [
        {"name": "auth", "description": "Authentication and tokens"},
        {"name": "reception", "description": "AI reception: chat, voice, handoff"},
        {"name": "guests", "description": "Guest profiles, documents, consent"},
        {"name": "rooms", "description": "Rooms, types, rates, availability"},
        {"name": "reservations", "description": "Booking and check-in/out"},
        {"name": "housekeeping", "description": "Task queue and priority engine"},
        {"name": "restaurant", "description": "Menu, orders, kitchen display"},
        {"name": "billing", "description": "Folio, invoice, payment, checkout"},
        {"name": "vision", "description": "Face recognition and document OCR"},
        {"name": "rag", "description": "Knowledge base and retrieval"},
        {"name": "search", "description": "Semantic image and room search"},
        {"name": "ai-center", "description": "Model config, prompts, observability"},
    ],
}

CORS_ALLOW_CREDENTIALS = True
CORS_ALLOWED_ORIGINS = env.list("DJANGO_CORS_ALLOWED_ORIGINS", default=[])

# ==============================================================================
# I18N — Bangla + English day one (goal.txt §6)
# ==============================================================================
LANGUAGE_CODE = env("DJANGO_LANGUAGE_CODE", default="en-us")
TIME_ZONE = env("DJANGO_TIME_ZONE", default="Asia/Dhaka")
USE_I18N = True
USE_TZ = True
LANGUAGES = [("en", "English"), ("bn", "বাংলা")]
LOCALE_PATHS = [BASE_DIR / "locale"]

# ==============================================================================
# Static / media
# ==============================================================================
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

USE_S3 = env.bool("USE_S3", default=False)

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

if USE_S3:
    STORAGES["default"] = {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "access_key": env("AWS_ACCESS_KEY_ID", default=""),
            "secret_key": env("AWS_SECRET_ACCESS_KEY", default=""),
            "bucket_name": env("AWS_STORAGE_BUCKET_NAME", default="ashos-media"),
            "endpoint_url": env("AWS_S3_ENDPOINT_URL", default=None),
            "region_name": env("AWS_S3_REGION_NAME", default="us-east-1"),
            "querystring_auth": True,
            "file_overwrite": False,
            "default_acl": None,
        },
    }

FILE_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 25 * 1024 * 1024

# ==============================================================================
# Security defaults (prod.py tightens further)
# ==============================================================================
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_HTTPONLY = False  # the kiosk JS reads it to post the token

# Encrypted model fields (provider keys, biometric embeddings). If unset the key
# derives from SECRET_KEY — convenient in dev, but then rotating SECRET_KEY makes
# every ciphertext unreadable. Production must set this explicitly.
FIELD_ENCRYPTION_KEY = env("FIELD_ENCRYPTION_KEY", default="")

# ==============================================================================
# AI GATEWAY BOOTSTRAP (goal.txt D07/D08) — database overrides these at runtime.
# ==============================================================================
AI = {
    "ENABLED": env.bool("AI_ENABLED", default=True),
    "KILL_SWITCH": env.bool("AI_KILL_SWITCH", default=False),
    "CONFIDENCE_THRESHOLD": env.float("AI_CONFIDENCE_THRESHOLD", default=0.55),
    "MAX_CONVERSATION_TURNS": env.int("AI_MAX_CONVERSATION_TURNS", default=30),
    "SESSION_TOKEN_CAP": env.int("AI_SESSION_TOKEN_CAP", default=20_000),
    "DAILY_COST_CAP_USD": env.float("AI_DAILY_COST_CAP_USD", default=25.0),
    "COST_ALERT_WEBHOOK": env("AI_COST_ALERT_WEBHOOK", default=""),
    "LLM": {
        "provider": env("AI_LLM_PROVIDER", default="openai_compatible"),
        "base_url": env("AI_LLM_BASE_URL", default="https://api.openai.com/v1"),
        "api_key": env("AI_LLM_API_KEY", default=""),
        "model": env("AI_LLM_MODEL", default="gpt-4o-mini"),
        "temperature": env.float("AI_LLM_TEMPERATURE", default=0.2),
        "max_tokens": env.int("AI_LLM_MAX_TOKENS", default=1024),
        "timeout_s": env.int("AI_LLM_TIMEOUT_S", default=30),
        "fallback_model": env("AI_LLM_FALLBACK_MODEL", default=""),
    },
    "EMBEDDING": {
        "provider": env("AI_EMBEDDING_PROVIDER", default="openai_compatible"),
        "base_url": env("AI_EMBEDDING_BASE_URL", default="https://api.openai.com/v1"),
        "api_key": env("AI_EMBEDDING_API_KEY", default=""),
        "model": env("AI_EMBEDDING_MODEL", default="text-embedding-3-small"),
        "dimension": env.int("AI_EMBEDDING_DIM", default=1536),
    },
    "IMAGE_EMBEDDING": {
        "provider": env("AI_IMAGE_EMBEDDING_PROVIDER", default="local_clip"),
        "model": env("AI_IMAGE_EMBEDDING_MODEL", default="ViT-B-32"),
        "dimension": env.int("AI_IMAGE_EMBEDDING_DIM", default=512),
    },
    "FACE": {
        "provider": env("AI_FACE_PROVIDER", default="local_insightface"),
        "model": env("AI_FACE_MODEL", default="buffalo_l"),
        "dimension": env.int("AI_FACE_DIM", default=512),
        "match_threshold": env.float("AI_FACE_MATCH_THRESHOLD", default=0.38),
        "liveness_required": env.bool("AI_FACE_LIVENESS_REQUIRED", default=True),
    },
    "STT": {
        "provider": env("AI_STT_PROVIDER", default="openai_compatible"),
        "model": env("AI_STT_MODEL", default="whisper-1"),
    },
    "TTS": {
        "provider": env("AI_TTS_PROVIDER", default="openai_compatible"),
        "model": env("AI_TTS_MODEL", default="tts-1"),
        # A female voice by default, as hotel reception convention expects.
        # Override per hotel in AI Center -> TTS config -> extra {"voice": "..."}.
        "voice": env("AI_TTS_VOICE", default="nova"),
    },
    "OCR": {
        "provider": env("AI_OCR_PROVIDER", default="paddleocr"),
        "langs": env.list("AI_OCR_LANGS", default=["en", "bn"]),
    },
}

# Vector dimensions are referenced by migrations. Changing one is a re-embed
# migration, never an in-place edit (goal.txt D08).
VECTOR_DIMENSIONS = {
    "text": AI["EMBEDDING"]["dimension"],
    "image": AI["IMAGE_EMBEDDING"]["dimension"],
    "face": AI["FACE"]["dimension"],
}

# ==============================================================================
# Biometric privacy (goal.txt D10)
# ==============================================================================
BIOMETRIC = {
    # Platform switch. A property's own ``Hotel.biometric_enabled`` must also be
    # on; either off means off, so one mis-set variable cannot enable face
    # capture across every tenant.
    "ENABLED": env.bool("BIOMETRIC_ENABLED", default=False),
    "RETENTION_DAYS": env.int("BIOMETRIC_RETENTION_DAYS", default=90),
    # Keep the captured frames as images, so a receptionist can compare the
    # arriving guest against what was taken at booking.
    #
    # Default OFF, and deliberately env-gated rather than hardcoded: storing a
    # photograph of a face is a decision with legal weight, and it belongs to the
    # operator who signed the DPIA — not to whoever last edited this file. With
    # it off the flow still runs and consent is still recorded; the image column
    # simply stays empty.
    "STORE_RAW_IMAGE": env.bool("BIOMETRIC_STORE_RAW_IMAGE", default=False),
    "MIN_AGE": 18,
}

# ==============================================================================
# Notifications
# ==============================================================================
NOTIFICATIONS = {
    "SMS_PROVIDER": env("SMS_PROVIDER", default="console"),
    "SMS_API_KEY": env("SMS_API_KEY", default=""),
    "SMS_SENDER_ID": env("SMS_SENDER_ID", default="ASHOS"),
    "WHATSAPP_PROVIDER": env("WHATSAPP_PROVIDER", default="console"),
    "WHATSAPP_API_KEY": env("WHATSAPP_API_KEY", default=""),
    "VAPID_PUBLIC_KEY": env("VAPID_PUBLIC_KEY", default=""),
    "VAPID_PRIVATE_KEY": env("VAPID_PRIVATE_KEY", default=""),
}

EMAIL_BACKEND = env("EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend")
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="noreply@ashos.local")

# ==============================================================================
# Payments (Q4 unresolved — manual provider keeps billing testable)
# ==============================================================================
PAYMENTS = {
    "PROVIDER": env("PAYMENT_PROVIDER", default="manual"),
    "SANDBOX": env.bool("PAYMENT_SANDBOX", default=True),
    "STORE_ID": env("PAYMENT_STORE_ID", default=""),
    "STORE_SECRET": env("PAYMENT_STORE_SECRET", default=""),
}

# ==============================================================================
# Logging — structlog, JSON in prod
# ==============================================================================
LOG_LEVEL = env("LOG_LEVEL", default="INFO")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "plain": {
            "format": "{asctime} {levelname:<7} {name} :: {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "plain"},
    },
    "root": {"handlers": ["console"], "level": LOG_LEVEL},
    "loggers": {
        "django.db.backends": {"level": "WARNING", "propagate": True},
        "ashos": {"level": LOG_LEVEL, "propagate": True},
        "ashos.ai": {"level": LOG_LEVEL, "propagate": True},
    },
}
