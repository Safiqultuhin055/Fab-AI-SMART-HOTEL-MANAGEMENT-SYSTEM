from __future__ import annotations

from django.contrib.auth.decorators import login_required, permission_required
from django.shortcuts import render
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.views import module_page
from services.analytics import kpi
from services.booking import reservations as booking


@login_required
def home(request):
    """Operations dashboard (Prototype.png S1).

    Every tile is now a real query. Where a number cannot be computed yet the
    tile still says which phase brings it, rather than showing a zero that
    looks like a fact.
    """
    request.active_nav = "dashboard"
    hotel = getattr(request, "tenant", None)

    if hotel is None:
        return render(
            request,
            "dashboard/home.html",
            {"page_title": _("Dashboard"), "kpis": [], "hotel": None},
        )

    today = kpi.for_day(hotel)
    deltas = kpi.comparison(hotel)
    currency = hotel.currency

    kpis = [
        {
            "key": "checkins",
            "label": _("Today Check-ins"),
            "value": today.arrivals,
            "delta": None,
        },
        {
            "key": "occupancy",
            "label": _("Occupancy"),
            "value": f"{today.occupancy_rate:.0f}",
            "suffix": "%",
            "delta": deltas["occupancy"],
        },
        {
            "key": "revenue",
            "label": _("Today Revenue"),
            "value": f"{kpi.revenue_today(hotel):,.0f}",
            "prefix": f"{currency} ",
            "delta": deltas["revenue"],
        },
        {
            "key": "available",
            "label": _("Rooms Available"),
            "value": today.available,
            "sub": f"/ {today.total_rooms} {_('sellable')}",
        },
        {
            "key": "revpar",
            "label": _("RevPAR"),
            "value": f"{today.revpar:,.0f}",
            "prefix": f"{currency} ",
            "delta": deltas["revpar"],
        },
    ]

    return render(
        request,
        "dashboard/home.html",
        {
            "page_title": _("Dashboard"),
            "hotel": hotel,
            "kpis": kpis,
            "stats": today,
            "adr": today.adr,
            "room_status": kpi.room_status_breakdown(hotel),
            "recent": kpi.recent_bookings(hotel),
            "arrivals": booking.arrivals(hotel)[:6],
            "departures": booking.departures(hotel)[:6],
            "outstanding": kpi.outstanding_balance(hotel),
            "payments_today": kpi.payments_today(hotel),
            "ai": kpi.ai_snapshot(hotel),
            "trend": kpi.trend(hotel, days=14),
            "business_date": timezone.localdate(),
        },
    )


@login_required
@permission_required("core.access_reports", raise_exception=True)
def reports(request):
    return module_page(request, "reports")
