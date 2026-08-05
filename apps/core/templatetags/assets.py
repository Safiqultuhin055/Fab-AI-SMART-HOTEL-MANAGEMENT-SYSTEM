"""``{% asset %}`` — ``{% static %}`` that cannot be served stale.

The problem it solves is small and expensive. Django's development static handler
sends ``Last-Modified`` and no ``Cache-Control``. With no explicit directive the
browser applies its own heuristic, and it reuses often enough that an edited
stylesheet or script silently does not load on an ordinary reload. What you see
is a UI that looks broken and a string you deleted still on screen, and the half
hour that follows is spent debugging code that is already correct.

Middleware cannot fix it: ``runserver`` serves ``/static/`` from a handler that
wraps the whole WSGI application, so those requests never reach the middleware
chain.

So the version goes in the URL. In DEBUG that is the file's modification time —
precise, so editing one file busts one URL. In production it does nothing at all,
because WhiteNoise's manifest storage already puts a content hash in the
filename; adding a query string there would only defeat the CDN.
"""

from __future__ import annotations

from pathlib import Path

from django import template
from django.conf import settings
from django.contrib.staticfiles import finders
from django.templatetags.static import static

register = template.Library()


@register.simple_tag
def asset(path: str) -> str:
    url = static(path)
    if not settings.DEBUG:
        return url

    # finders.find hits the disk. Fine here: DEBUG only, and a handful of files
    # per page render.
    found = finders.find(path)
    if not found:
        return url
    try:
        stamp = int(Path(found).stat().st_mtime)
    except OSError:  # pragma: no cover - file vanished between find and stat
        return url

    separator = "&" if "?" in url else "?"
    return f"{url}{separator}v={stamp}"
