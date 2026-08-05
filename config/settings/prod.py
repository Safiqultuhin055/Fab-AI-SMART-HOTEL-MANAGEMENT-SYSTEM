"""Production settings. Fails loudly rather than starting up insecure."""

from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403
from .base import LOGGING, env

DEBUG = False

SECRET_KEY = env("DJANGO_SECRET_KEY")
if SECRET_KEY.startswith("insecure"):
    raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set to a real secret in production.")

ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS")
if not ALLOWED_HOSTS:
    raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS must be set in production.")

# --- TLS everywhere (goal.txt §13.1) -----------------------------------------
SECURE_SSL_REDIRECT = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_HSTS_SECONDS = 31_536_000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# --- Structured logs ----------------------------------------------------------
LOGGING["formatters"]["json"] = {
    "()": "django_structlog.formatters.JSONFormatter",
}
LOGGING["handlers"]["console"]["formatter"] = "json"

# --- Error tracking -----------------------------------------------------------
SENTRY_DSN = env("SENTRY_DSN", default="")
if SENTRY_DSN:
    import sentry_sdk
    from sentry_sdk.integrations.celery import CeleryIntegration
    from sentry_sdk.integrations.django import DjangoIntegration

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        integrations=[DjangoIntegration(), CeleryIntegration()],
        traces_sample_rate=env.float("SENTRY_TRACES_SAMPLE_RATE", default=0.1),
        send_default_pii=False,  # guest data must never leave the perimeter
        environment=env("SENTRY_ENVIRONMENT", default="production"),
    )
