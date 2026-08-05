"""Root URL configuration.

Server-rendered staff UI lives at the root; the JSON API lives under
``/api/v1/`` and is the only surface the Guest PWA and kiosk talk to.
"""

from __future__ import annotations

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)

urlpatterns = [
    path("admin/", admin.site.urls),
    # --- JSON API -------------------------------------------------------------
    path("api/v1/", include(("api.v1.urls", "api"), namespace="v1")),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
    path("api/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
    # --- Staff web UI ---------------------------------------------------------
    path("accounts/", include("apps.accounts.urls", namespace="accounts")),
    path("reception/", include("apps.reception.urls", namespace="reception")),
    path("guests/", include("apps.guests.urls", namespace="guests")),
    path("rooms/", include("apps.rooms.urls", namespace="rooms")),
    path("reservations/", include("apps.booking.urls", namespace="booking")),
    # Public, no login — like the lobby kiosk, and for the same reason: the people
    # it is for do not have accounts. Tenant comes from ?hotel=<code>.
    path("book/", include("apps.booking.public_urls", namespace="online_booking")),
    path("housekeeping/", include("apps.housekeeping.urls", namespace="housekeeping")),
    path("restaurant/", include("apps.restaurant.urls", namespace="restaurant")),
    path("billing/", include("apps.billing.urls", namespace="billing")),
    path("ai-center/", include("apps.ai_center.urls", namespace="ai_center")),
    path("settings/", include("apps.tenants.urls", namespace="tenants")),
    # Dashboard owns "" so it must be registered last.
    path("", include("apps.dashboard.urls", namespace="dashboard")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

admin.site.site_header = "ASHOS Administration"
admin.site.site_title = "ASHOS"
admin.site.index_title = "AI Smart Hotel OS"
