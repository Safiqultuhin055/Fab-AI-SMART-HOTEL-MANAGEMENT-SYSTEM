"""Resolve the hotel this request operates on.

Resolution order, most explicit first:
  1. ``X-Hotel-Code`` header      — kiosk and Guest PWA clients pin their hotel
  2. ``?hotel=<code>`` query      — deep links from email/reports
  3. session ``hotel_id``         — staff who switched hotel in the UI
  4. the user's default membership

Anonymous requests get no tenant, which makes tenant-scoped managers return
empty rather than leaking. Public endpoints (booking portal) must resolve the
hotel from the URL and set it explicitly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from apps.core.context import set_request_context

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Callable

    from django.http import HttpRequest, HttpResponse

SESSION_KEY = "ashos_hotel_id"
HEADER = "HTTP_X_HOTEL_CODE"


class CurrentTenantMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        hotel = self._resolve(request)
        request.tenant = hotel  # type: ignore[attr-defined]
        if hotel is not None:
            set_request_context(tenant_id=str(hotel.pk))
        return self.get_response(request)

    def _resolve(self, request: HttpRequest):
        # Imported lazily: middleware is constructed before the app registry is
        # guaranteed ready in some management commands.
        from apps.tenants.models import Hotel, HotelMembership

        code = request.META.get(HEADER) or request.GET.get("hotel")
        if code:
            hotel = Hotel.objects.filter(code=code.upper(), is_active=True).first()
            if hotel is not None and hasattr(request, "session"):
                # Remember it. A lobby kiosk loads the page once with
                # ?hotel=CODE and then makes many XHR calls that carry no query
                # string; without this every one of them would resolve to no
                # tenant and fail.
                request.session[SESSION_KEY] = str(hotel.pk)
            return hotel

        session_id = request.session.get(SESSION_KEY) if hasattr(request, "session") else None
        if session_id:
            hotel = Hotel.objects.filter(pk=session_id, is_active=True).first()
            if hotel:
                return hotel

        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            membership = (
                HotelMembership.objects.filter(user=user, hotel__is_active=True)
                .select_related("hotel")
                .order_by("-is_default", "created_at")
                .first()
            )
            if membership:
                if hasattr(request, "session"):
                    request.session[SESSION_KEY] = str(membership.hotel_id)
                return membership.hotel

        return None
