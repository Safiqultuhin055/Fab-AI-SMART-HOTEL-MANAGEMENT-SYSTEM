"""Local development settings."""

from __future__ import annotations

import socket
from urllib.parse import urlparse

from .base import *  # noqa: F403
from .base import CELERY_BROKER_URL, INSTALLED_APPS, REDIS_URL, REST_FRAMEWORK, env

DEBUG = env.bool("DJANGO_DEBUG", default=True)
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS += ["django_extensions"]

# Browsable API is a genuine productivity win while the frontend is catching up.
REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"] = (
    "rest_framework.renderers.JSONRenderer",
    "rest_framework.renderers.BrowsableAPIRenderer",
)

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# ==============================================================================
# Redis is optional in development
# ==============================================================================
# base.py points the cache, the channel layer and the Celery broker at Redis. A
# missing Redis does not degrade gracefully there: the session and the navigation
# cache are read before any view runs, so *every* URL — including the login page
# — answers 500 with a ConnectionError. Docker Desktop being closed is a normal
# state on a dev machine, and "the app is completely down" is the wrong response
# to it.
#
# So probe the socket once at startup and fall back to in-process backends.
#
# The fallbacks are per-process and vanish on restart. That is fine for a single
# runserver and wrong for anything needing two processes to agree: a separate
# Celery worker sees none of it, and websocket groups do not cross processes.
# When that matters, start Redis and this file gets out of the way:
#
#   docker compose -f deploy/compose.yml --env-file .env up -d redis
#
# Set DEV_REQUIRE_REDIS=true to disable the fallback and get the connection error
# instead — worth doing before blaming the code for a cache that "isn't caching".


def _port_is_open(url: str, timeout: float = 0.35) -> bool:
    parsed = urlparse(url)
    host, port = parsed.hostname or "127.0.0.1", parsed.port or 6379
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            return sock.connect_ex((host, port)) == 0
    except OSError:
        return False


DEV_REQUIRE_REDIS = env.bool("DEV_REQUIRE_REDIS", default=False)
_REDIS_UP = DEV_REQUIRE_REDIS or _port_is_open(REDIS_URL)

if not _REDIS_UP:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "ashos-dev",
            "KEY_PREFIX": "ashos",
        }
    }
    CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}

# Run tasks inline when no broker is up, so a fresh clone works without Docker.
CELERY_TASK_ALWAYS_EAGER = env.bool(
    "CELERY_TASK_ALWAYS_EAGER",
    default=not (DEV_REQUIRE_REDIS or _port_is_open(CELERY_BROKER_URL)),
)
CELERY_TASK_EAGER_PROPAGATES = True

# Assets are referenced with {% asset %} rather than {% static %}, which appends
# the file's modification time in DEBUG. Without it the browser holds a stale
# stylesheet across a normal reload — CSS that "did not apply", JS still showing
# a string you deleted — because the dev static handler sends no Cache-Control
# and the browser is then free to guess.
#
# Not solvable with middleware: runserver's StaticFilesHandler wraps the whole
# WSGI app, so /static/ never reaches the middleware chain.
WHITENOISE_AUTOREFRESH = True
WHITENOISE_MAX_AGE = 0
