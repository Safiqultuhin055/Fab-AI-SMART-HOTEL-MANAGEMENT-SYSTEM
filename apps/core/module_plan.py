"""What each module will contain, and when.

Rendered on the module page itself. Putting the roadmap in the product means a
manager clicking Housekeeping learns what it will do and when, instead of
finding a dead menu item and assuming the software is broken.

Sources: goal.txt §4.1 (scope) and §5 (phases); SRS module sections.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModulePlan:
    key: str
    title: str
    phase: str
    summary: str
    features: tuple[str, ...]
    depends_on: tuple[str, ...] = ()


MODULE_PLANS: dict[str, ModulePlan] = {
    "reception": ModulePlan(
        key="reception",
        title="AI Reception",
        phase="P2",
        summary=(
            "The heart of ASHOS: a guest walks up and talks. Voice, text, face and "
            "documents all resolve to one conversation, and a human is one tap away "
            "whenever the AI is unsure."
        ),
        features=(
            "AI avatar receptionist on the kiosk, full screen, lip-synced",
            "Voice conversation: Whisper → LLM → TTS, under 3 seconds round trip",
            "Streaming text chat in Bangla, English, Hindi, Arabic and Chinese",
            "Face recognition for returning guests, with liveness checks",
            "Passport / NID OCR with MRZ checksum validation",
            "RAG concierge answers with source citations, never invention",
            "Automatic handoff to human staff below the confidence threshold",
            "Live conversation monitor and handoff queue for the front desk",
        ),
        depends_on=("RAG knowledge base", "Vision pipeline", "AI Center prompts"),
    ),
    "guests": ModulePlan(
        key="guests",
        title="Guests",
        phase="P1",
        summary=(
            "One profile per person, across every stay. Preferences, documents and "
            "consent live here, and everything else in the system points at it."
        ),
        features=(
            "Guest profile with stay history and lifetime value",
            "Preferences that feed AI room recommendation",
            "Passport / NID / visa documents with expiry tracking",
            "Explicit biometric consent ledger, with revocation",
            "Face enrolment (opt-in only, embedding stored, image discarded)",
            "Right-to-erasure: delete every trace of a guest on request",
            "Duplicate detection for transliterated names",
        ),
    ),
    "rooms": ModulePlan(
        key="rooms",
        title="Rooms & Inventory",
        phase="P1",
        summary=(
            "Room types, rate plans and the live status board that reception, "
            "housekeeping and the AI all read from."
        ),
        features=(
            "Room types, amenities and occupancy limits",
            "Status board: Vacant Clean · Vacant Dirty · Occupied · Out of Order",
            "Rate plans with seasonal and day-of-week pricing",
            "Room image gallery, CLIP-indexed for semantic search",
            "Maintenance blocks and out-of-order tracking",
        ),
    ),
    "reservations": ModulePlan(
        key="reservations",
        title="Reservations",
        phase="P1",
        summary=(
            "Booking, check-in and check-out. Double-booking is prevented by a "
            "database constraint, not by hoping the UI is correct."
        ),
        features=(
            "Online booking portal and walk-in booking at the desk",
            "Real-time availability with an exclusion constraint on overlapping stays",
            "QR check-in and face check-in",
            "Digital signature capture, stored immutably",
            "AI room recommendation from stated preference plus current availability",
            "Check-out settlement handoff to Billing",
        ),
    ),
    "housekeeping": ModulePlan(
        key="housekeeping",
        title="Housekeeping",
        phase="P4",
        summary=(
            "A priority queue that explains itself. The ordering is a weighted rule "
            "engine, not an LLM, so a supervisor can always ask why a room is first "
            "and get a real answer."
        ),
        features=(
            "Dirty room queue with AI priority score",
            "Weights tunable from AI Center: arrival ETA, VIP tier, dirty duration, "
            "floor proximity, early check-in requests",
            "Task assignment and per-attendant workload",
            "Cleaning status transitions with timestamps",
            "Photo verification before a room is released",
        ),
    ),
    "restaurant": ModulePlan(
        key="restaurant",
        title="Restaurant & POS",
        phase="P4",
        summary=(
            "Deliberately kept small: menu, order, kitchen, post to folio. A full "
            "F&B suite is a different product."
        ),
        features=(
            "Menu with categories, items and modifiers",
            "Dine-in and room-service orders",
            "Kitchen display system over WebSocket",
            "Post charges straight to the guest folio",
        ),
    ),
    "billing": ModulePlan(
        key="billing",
        title="Billing & Finance",
        phase="P1",
        summary=(
            "Folio-based billing with a night audit that balances. The AI may "
            "propose a discount or an upsell; a human approves anything financial."
        ),
        features=(
            "Guest folio with itemised charges from every module",
            "Invoice with VAT and service charge",
            "Payments: cash, card, bKash, Nagad, bank transfer",
            "Split bill and corporate billing",
            "Night audit: roll the business date, post room charges",
            "Proactive checkout reminder 12 hours ahead, in the guest's language",
            "Export for external accounting; no double-entry ledger here",
        ),
    ),
    "reports": ModulePlan(
        key="reports",
        title="Reports & Analytics",
        phase="P4",
        summary=(
            "Occupancy, ADR, RevPAR and AI economics on one page, with forecasts "
            "once there is enough history to forecast from."
        ),
        features=(
            "Occupancy, ADR and RevPAR trends",
            "Revenue by source, room type and channel",
            "Guest satisfaction from post-stay surveys",
            "AI cost per guest-stay against the budget cap",
            "Most-asked guest questions — the shortlist for new knowledge articles",
            "Occupancy and revenue forecasting",
        ),
    ),
}
