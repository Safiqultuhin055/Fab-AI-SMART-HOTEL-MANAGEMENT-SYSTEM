"""Public online-booking routes, mounted outside the staff prefix.

Its own module rather than lines in ``apps/booking/urls.py``: everything in there
is login-gated and lives under /reservations/, and a guest-facing URL that reads
like a staff screen is one nobody will put on a hotel's website.
"""

from __future__ import annotations

from django.urls import path

from apps.booking import public_views

app_name = "online_booking"

urlpatterns = [
    path("", public_views.book, name="book"),
]
