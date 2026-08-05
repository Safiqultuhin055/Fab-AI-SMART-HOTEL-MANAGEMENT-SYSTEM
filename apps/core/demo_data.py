"""Static pools for demo-data generation.

Kept out of the management command so tests and future fixtures can reuse them,
and so the command itself stays readable.

Names are a realistic Bangladesh/South-Asia mix with some international guests,
because that is who the pilot segment actually serves (goal.txt §3.2). Seeding
with "John Doe 1..50" hides real problems: Bangla name rendering, long names
wrapping in the sidebar, and transliterated duplicates during walk-in lookup.
"""

from __future__ import annotations

BANGLA_NAMES: tuple[str, ...] = (
    "Rina Haque",
    "Tanvir Ahmed",
    "Nusrat Jahan",
    "Mahmudul Hasan",
    "Sadia Islam",
    "Rakibul Karim",
    "Farhana Akter",
    "Imran Chowdhury",
    "Sharmin Sultana",
    "Arif Mahmud",
    "Jubayer Rahman",
    "Tasnim Nahar",
    "Shahriar Kabir",
    "Mitu Barua",
    "Nayeem Uddin",
    "Rezwana Choudhury",
    "Sabbir Hossain",
    "Ishrat Binte Alam",
    "Mizanur Rahman",
    "Priya Das",
)

INTERNATIONAL_NAMES: tuple[str, ...] = (
    "Daniel Whitfield",
    "Ayesha Siddiqui",
    "Kenji Nakamura",
    "Marta Kowalski",
    "Omar Al-Rashid",
    "Li Wei",
    "Sophie Bernard",
    "Rajesh Menon",
)

# Hotels the seeder owns. --flush removes exactly these and nothing else, so a
# real property created by hand is never destroyed by a demo reset.
DEMO_HOTELS: tuple[dict[str, object], ...] = (
    {
        "code": "SPR-002",
        "name": "Sea Pearl Resort",
        "city": "Cox's Bazar",
        "rooms": 86,
        "stars": 4,
        "accent": "#0ea5e9",
        "plan": "standard",
    },
    {
        "code": "DSS-003",
        "name": "Dhaka Serviced Suites",
        "city": "Dhaka",
        "rooms": 42,
        "stars": 3,
        "accent": "#10b981",
        "plan": "pilot",
    },
)

# Marks every user the seeder creates. Flush matches on this prefix.
DEMO_EMPLOYEE_PREFIX = "DEMO-"

# Documented in runcommand.txt. Meets the 10-character minimum and the
# similarity/common-password validators.
DEMO_PASSWORD = "Demo@12345"  # noqa: S105 - documented demo credential, dev only

# --- AI usage shapes ----------------------------------------------------------
# (module, kind, latency_p50_ms, latency_spread, in_tokens, out_tokens, weight)
# Weights approximate a real day: the concierge dominates, OCR is rare but slow,
# embeddings are frequent and cheap. Flat-random usage would make the AI Center
# latency and cost panels look wrong in a way nobody notices until production.
AI_CALL_SHAPES: tuple[tuple[str, str, int, int, int, int, int], ...] = (
    ("reception", "llm", 900, 700, 850, 180, 40),
    ("rag", "embedding", 120, 90, 210, 0, 25),
    ("reception", "stt", 620, 380, 0, 0, 12),
    ("reception", "tts", 430, 260, 0, 0, 10),
    ("vision", "face", 340, 180, 0, 0, 6),
    ("vision", "ocr", 1550, 900, 0, 0, 3),
    ("housekeeping", "llm", 700, 400, 620, 140, 2),
    ("notifications", "llm", 650, 300, 300, 90, 2),
)

# Errors a real deployment actually produces, with plausible frequencies.
AI_ERROR_CODES: tuple[str, ...] = (
    "ai_timeout",
    "rate_limited",
    "provider_5xx",
    "context_length_exceeded",
)

# --- PMS inventory ------------------------------------------------------------
# Everything the seeder creates is tagged so --flush can find it again without
# touching rooms or guests an operator entered by hand.
DEMO_MARKER = "demo-seed"
DEMO_GUEST_DOMAIN = "demo.ashos.local"

# code, name, beds, base occ, max occ, bed type, base rate (BDT), view, share of floor
ROOM_TYPES: tuple[dict[str, object], ...] = (
    {
        "code": "STD",
        "name": "Standard Double",
        "base_occupancy": 2,
        "max_occupancy": 3,
        "bed_type": "double",
        "base_rate": "4500.00",
        "extra_person_rate": "900.00",
        "size_sqm": 24,
        "view": "city",
        "share": 0.40,
    },
    {
        "code": "DLX",
        "name": "Deluxe King",
        "base_occupancy": 2,
        "max_occupancy": 3,
        "bed_type": "king",
        "base_rate": "7200.00",
        "extra_person_rate": "1200.00",
        "size_sqm": 32,
        "view": "city",
        "share": 0.28,
    },
    {
        "code": "SEA",
        "name": "Sea View Suite",
        "base_occupancy": 2,
        "max_occupancy": 4,
        "bed_type": "king",
        "base_rate": "12500.00",
        "extra_person_rate": "1500.00",
        "size_sqm": 48,
        "view": "sea",
        "share": 0.17,
    },
    {
        "code": "TWN",
        "name": "Twin Economy",
        "base_occupancy": 2,
        "max_occupancy": 2,
        "bed_type": "twin",
        "base_rate": "3200.00",
        "extra_person_rate": "0.00",
        "size_sqm": 20,
        "view": "garden",
        "share": 0.10,
    },
    {
        "code": "FAM",
        "name": "Family Room",
        "base_occupancy": 4,
        "max_occupancy": 6,
        "bed_type": "double",
        "base_rate": "9800.00",
        "extra_person_rate": "1100.00",
        "size_sqm": 55,
        "view": "garden",
        "share": 0.05,
    },
)

AMENITIES: tuple[str, ...] = (
    "Air conditioning",
    "Free Wi-Fi",
    "Smart TV",
    "Minibar",
    "Safe",
    "Balcony",
    "Bathtub",
    "Tea & coffee",
    "Work desk",
    "Blackout curtains",
)

RATE_PLANS: tuple[dict[str, object], ...] = (
    {
        "code": "BAR",
        "name": "Best Available Rate",
        "discount_percent": "0.00",
        "includes_breakfast": False,
        "is_refundable": True,
        "is_default": True,
    },
    {
        "code": "BB",
        "name": "Bed & Breakfast",
        "discount_percent": "-8.00",
        "includes_breakfast": True,
        "is_refundable": True,
        "is_default": False,
    },
    {
        "code": "NR",
        "name": "Non-refundable",
        "discount_percent": "12.00",
        "includes_breakfast": False,
        "is_refundable": False,
        "is_default": False,
    },
    {
        "code": "CORP",
        "name": "Corporate",
        "discount_percent": "18.00",
        "includes_breakfast": True,
        "is_refundable": True,
        "is_default": False,
    },
)

SPECIAL_REQUESTS: tuple[str, ...] = (
    "",
    "",
    "",
    "High floor if possible.",
    "Late arrival, around 23:00.",
    "Extra pillows please.",
    "Quiet room away from the lift.",
    "Airport pickup required.",
    "Celebrating an anniversary.",
)

EXTRA_CHARGES: tuple[tuple[str, str, str, str], ...] = (
    ("restaurant", "Dinner — The Grand Restaurant", "1850.00", "2400.00"),
    ("restaurant", "Room service breakfast", "650.00", "1200.00"),
    ("laundry", "Laundry service", "400.00", "900.00"),
    ("minibar", "Minibar consumption", "350.00", "1100.00"),
    ("spa", "Spa treatment", "2500.00", "4500.00"),
    ("transport", "Airport transfer", "1200.00", "1800.00"),
)

AUDIT_EVENTS: tuple[tuple[str, str], ...] = (
    ("login", "{actor} signed in"),
    ("logout", "{actor} signed out"),
    ("login_failed", "failed sign-in for {actor}"),
    ("update", "{actor} updated hotel profile"),
    ("permission", "{actor} changed role permissions"),
    ("ai_override", "{actor} toggled the AI kill switch"),
    ("export", "{actor} exported the guest list"),
)

# Public list pricing at the time of writing, USD per 1k tokens. Seeded onto the
# demo ModelConfig rows so the cost rollup and budget cap have real numbers to
# act on instead of zeros.
MODEL_PRICING: dict[str, tuple[str, str]] = {
    "llm": ("0.000150", "0.000600"),
    "embedding": ("0.000020", "0.000000"),
    "image_embedding": ("0.000000", "0.000000"),
    "face": ("0.000000", "0.000000"),
    "stt": ("0.000100", "0.000000"),
    "tts": ("0.000150", "0.000000"),
    "ocr": ("0.000000", "0.000000"),
}
