from __future__ import annotations

from datetime import timedelta

from django.contrib.auth.decorators import login_required, permission_required
from django.db.models import Avg, Count, Q
from django.shortcuts import render
from django.utils import timezone

from apps.core.views import module_page
from apps.reception.models import Conversation, ConversationStatus, Handoff
from apps.reception.panel import panel_context

MODULE_KEY = "reception"


@login_required
@permission_required("core.access_reception", raise_exception=True)
def home(request):
    """Staff reception console: the kiosk, plus what it is producing."""
    hotel = getattr(request, "tenant", None)
    since = timezone.now() - timedelta(days=1)

    conversations = Conversation.objects.filter(started_at__gte=since)
    stats = conversations.aggregate(
        total=Count("id"),
        handed_off=Count("id", filter=Q(status=ConversationStatus.HANDOFF)),
        resolved=Count("id", filter=Q(status=ConversationStatus.RESOLVED)),
        avg_turns=Avg("turn_count"),
    )
    total = stats["total"] or 0

    return module_page(
        request,
        MODULE_KEY,
        template="modules/reception.html",
        context={
            # lobby=False: this is a receptionist's own laptop. No camera, and no
            # microphone standing open in an office.
            **panel_context(hotel, lobby=False, channel="web"),
            "stats": {
                "total": total,
                "resolved": stats["resolved"] or 0,
                "handed_off": stats["handed_off"] or 0,
                # goal.txt §8 target: >=70% resolved without a human.
                "self_service_rate": ((stats["resolved"] or 0) / total * 100) if total else 0,
                "avg_turns": stats["avg_turns"] or 0,
            },
            "queue": (
                Handoff.objects.filter(resolved_at__isnull=True)
                .select_related("conversation", "claimed_by")
                .order_by("created_at")[:10]
            ),
            "recent": conversations.order_by("-started_at")[:12],
        },
    )


def kiosk(request):
    """Full-screen lobby terminal — no sidebar, no login.

    A kiosk cannot hold a staff session: it stands in a public lobby, unattended,
    all day. It identifies its property with ``?hotel=<code>`` (resolved by the
    tenant middleware) and can reach nothing beyond its own conversation.
    """
    hotel = getattr(request, "tenant", None)
    context = panel_context(hotel, lobby=True, channel="kiosk")
    return render(
        request,
        "reception/kiosk.html",
        {
            **context,
            "hotel": hotel,
            "page_title": context["kiosk_copy"]["page_title"],
        },
    )
