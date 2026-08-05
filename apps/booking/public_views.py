"""The public online-booking page — one screen, three panels.

    /book/?hotel=GLH-001

No login, like the lobby kiosk, and for the same reason: the people it is for do
not have accounts. The hotel comes from ``?hotel=<code>`` resolved by the tenant
middleware, and the page can reach nothing but availability, prices and its own
booking.

Booking, bill and slip sit side by side rather than as three steps behind each
other, because they answer three questions a guest asks at once: can I have it,
what does it cost, and what do I show at the desk. The bill fills in as soon as a
room is picked; the slip fills in when the booking is made.

Everything here is a thin call into ``services.booking.online``. A public page
holding its own idea of a price is a page that quotes yesterday's rate the first
time somebody changes one.
"""

from __future__ import annotations

from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from apps.core.exceptions import ASHOSError
from apps.reception import copy
from apps.reception.panel import panel_context
from services.billing import payment_policy
from services.booking import online


def _context(request, hotel, *, error: str = "") -> dict:
    """The whole page, for whatever the query string currently says.

    Re-read and re-priced on every request rather than kept in the session: a
    guest who leaves the tab open over a price change, or shares the URL, gets
    today's answer instead of a remembered one.
    """
    stay = None
    stay_error = ""
    try:
        stay = online.parse_stay(request.GET)
    except ASHOSError as exc:
        stay_error = str(exc)
        stay = online.parse_stay({})

    rooms = online.offers(hotel, stay) if hotel else []
    chosen = online.find_offer(hotel, stay, request.GET.get("room", "")) if hotel else None
    reference = (request.GET.get("ref") or "").strip().upper()

    return {
        # The assistant, exactly as the terminal and the console render it. lobby is
        # False: there is nobody standing at a machine here, so no camera and no
        # always-open microphone.
        **(panel_context(hotel, lobby=False, channel="website") if hotel else {}),
        "hotel": hotel,
        "stay": stay,
        "offers": rooms,
        "chosen": chosen,
        "error": error or stay_error,
        # Present only after a booking, and read from the folio rather than
        # recalculated: the slip and the bill settled at the desk must be the same
        # numbers.
        "slip": online.slip(hotel, reference) if hotel and reference else {},
        "reference": reference,
        "limits": {
            "nights": online.MAX_NIGHTS,
            "rooms": online.MAX_ROOMS,
            "advance": online.MAX_ADVANCE_DAYS,
        },
        # How the guest pays, in their language, from the property's own settings.
        # The page used to state this in hardcoded English — "No card needed, paid at
        # the desk" — which was true of the default and a lie about any property that
        # asks for an advance. It is also what the assistant now cites when a guest
        # asks, so page and conversation cannot disagree about money.
        "payment": {
            "lines": payment_policy.lines(hotel, _language(hotel)),
            "badge": payment_policy.badge(hotel, _language(hotel)),
        },
        # In the guest's language, from the same place the header takes its name.
        # It was the one string on this page written in English regardless of who was
        # reading it — and it is the browser tab, which is how somebody finds the page
        # again after switching away to check their dates.
        "page_title": copy.chrome(_language(hotel), "website")["page_title"],
    }


def _language(hotel) -> str:
    """The language this page is written in — the property's, not the browser's.

    Same rule as the kiosk: the assistant, the chrome and the payment terms all
    speak the property's configured language, and a guest switches all three at once
    with the language chip.
    """
    return (hotel.kiosk_language if hotel else "en") or "en"


@require_http_methods(["GET", "POST"])
def book(request):
    """Search and choose on GET; hold the room on POST.

    POST-then-redirect, so a guest who refreshes the confirmation does not make a
    second booking — the most expensive double-submit in a hotel.
    """
    hotel = getattr(request, "tenant", None)

    if request.method == "POST":
        if hotel is None:
            return render(request, "booking/online.html", _context(request, hotel), status=400)
        try:
            stay = online.parse_stay(request.POST)
            reservation = online.book(
                hotel,
                stay,
                room_code=request.POST.get("room", ""),
                guest_name=request.POST.get("guest_name", ""),
                guest_phone=request.POST.get("guest_phone", ""),
                guest_email=request.POST.get("guest_email", ""),
                requests=request.POST.get("requests", ""),
            )
        except ASHOSError as exc:
            # Back to the same page with the form still filled in, and the reason.
            request.GET = request.POST.copy()
            return render(request, "booking/online.html", _context(request, hotel, error=str(exc)))

        query = (
            f"?hotel={hotel.code}&ref={reservation.code}"
            f"&check_in={reservation.check_in.isoformat()}"
            f"&nights={reservation.nights}&room={request.POST.get('room', '')}"
        )
        return redirect(reverse("online_booking:book") + query)

    return render(request, "booking/online.html", _context(request, hotel))
