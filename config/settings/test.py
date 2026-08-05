"""Test settings — fast, hermetic, no external calls.

Any test that reaches a real AI provider must be marked ``ai_eval`` and is
excluded from the default CI run (goal.txt D14).
"""

from __future__ import annotations

from .base import *  # noqa: F403
from .base import AI, DATABASES, env

DEBUG = False
SECRET_KEY = "test-secret-key-not-used-anywhere-else"  # noqa: S105

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

# WhiteNoise's manifest storage demands a collectstatic run before any template
# containing {% static %} can render. Requiring that in the test suite would
# make every page test depend on a build step for no benefit.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# The fake provider returns deterministic responses; a real network call inside
# a unit test is a bug, not a flake.
AI["LLM"]["provider"] = "fake"
AI["EMBEDDING"]["provider"] = "fake"
AI["IMAGE_EMBEDDING"]["provider"] = "fake"
AI["FACE"]["provider"] = "fake"
AI["STT"]["provider"] = "fake"
AI["TTS"]["provider"] = "fake"
AI["OCR"]["provider"] = "fake"

# Face capture is pinned OFF for the suite regardless of the developer's .env.
# A test that only passes because somebody's local environment happens to have
# biometrics disabled is not testing the guard — and the tests that need it on
# turn it on explicitly with the ``settings`` fixture.
BIOMETRIC = {**BIOMETRIC, "ENABLED": False, "STORE_RAW_IMAGE": False}

DATABASES["default"]["TEST"] = {"NAME": env("TEST_DB_NAME", default="test_ashos")}

LOGGING = {"version": 1, "disable_existing_loggers": True, "root": {"handlers": []}}
