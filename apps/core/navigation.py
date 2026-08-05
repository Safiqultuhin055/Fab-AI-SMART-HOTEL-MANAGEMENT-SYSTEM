"""Left-navigation definition.

Order and labels are locked by Prototype.png (goal.txt §2.3).

Every item routes to a real page. Items whose module is still being built land
on that module's own page, which states what it will contain and in which
phase — a menu that silently does nothing is worse than one that explains
itself, and the page becomes the shell the real screens grow into.

Visibility is gated by ``core.access_*`` (see ``apps.core.models.ModuleAccess``):
coarse module-level permissions that exist independently of whether the module
has models yet.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class NavItem:
    key: str
    label: str
    icon: str
    url_name: str
    permission: str = ""
    phase: str = "P0"
    ready: bool = False
    children: tuple[NavItem, ...] = field(default_factory=tuple)


NAVIGATION: tuple[NavItem, ...] = (
    NavItem("dashboard", "Dashboard", "grid", "dashboard:home", "", "P0", ready=True),
    # Built: text conversation, guardrails, handoff queue, offline answers.
    # Voice needs a speech provider key; vision panels arrive in P2/P3.
    NavItem(
        "reception",
        "AI Reception",
        "sparkles",
        "reception:home",
        "core.access_reception",
        "P2",
        ready=True,
    ),
    NavItem("guests", "Guests", "users", "guests:home", "core.access_guests", "P1", ready=True),
    NavItem(
        "rooms",
        "Rooms & Inventory",
        "building",
        "rooms:home",
        "core.access_rooms",
        "P1",
        ready=True,
    ),
    NavItem(
        "reservations",
        "Reservations",
        "calendar",
        "booking:home",
        "core.access_reservations",
        "P1",
        ready=True,
    ),
    NavItem(
        "housekeeping",
        "Housekeeping",
        "broom",
        "housekeeping:home",
        "core.access_housekeeping",
        "P4",
    ),
    NavItem(
        "restaurant",
        "Restaurant & POS",
        "utensils",
        "restaurant:home",
        "core.access_restaurant",
        "P4",
    ),
    NavItem(
        "billing",
        "Billing & Finance",
        "receipt",
        "billing:home",
        "core.access_billing",
        "P1",
        ready=True,
    ),
    NavItem(
        "ai_center", "AI Center", "cpu", "ai_center:home", "core.access_ai_center", "P2", ready=True
    ),
    NavItem(
        "reports", "Reports & Analytics", "chart", "dashboard:reports", "core.access_reports", "P4"
    ),
    NavItem(
        "settings", "Settings", "cog", "tenants:settings", "core.access_settings", "P0", ready=True
    ),
)

QUICK_ACTIONS: tuple[NavItem, ...] = (
    # A link to a PUBLIC page, which is why it is here rather than in the module
    # list above. Every item in NAVIGATION is a permission-gated screen — the tests
    # assert that a role without the permission gets a 403 at the URL — and /book/
    # answers 200 to anyone by design, because the guests it is for have no
    # accounts. Menu visibility still follows access_reservations: it is the desk's
    # own shortcut for walking somebody through the site, or checking what the
    # website is quoting.
    NavItem(
        "online_booking",
        "Online Booking",
        "globe",
        "online_booking:book",
        "core.access_reservations",
        "P1",
        ready=True,
    ),
    NavItem(
        "walkin",
        "Walk-in Check-in",
        "bolt",
        "booking:home",
        "core.access_reservations",
        "P1",
        ready=True,
    ),
    NavItem(
        "new_reservation",
        "New Reservation",
        "plus",
        "booking:home",
        "core.access_reservations",
        "P1",
        ready=True,
    ),
    NavItem(
        "add_guest", "Add Guest", "user-plus", "guests:home", "core.access_guests", "P1", ready=True
    ),
    NavItem(
        "room_status", "Room Status", "layers", "rooms:home", "core.access_rooms", "P1", ready=True
    ),
)

NAV_BY_KEY = {item.key: item for item in NAVIGATION}
