"""Every word the kiosk says to a guest, in both languages, in one file.

Why this is not ``{% trans %}`` and a .po catalog
------------------------------------------------
Django's i18n picks a language per *request* from the browser's Accept-Language
header or the session. A lobby terminal has neither that means anything: the
browser is the hotel's own Chrome, opened once by a staff member, and the guest
standing in front of it changes language by tapping a chip mid-conversation. The
language belongs to the *conversation*, not to the HTTP request — so it is data
the server sends, not state the request carries.

That also makes switching cheap and immediate. Both languages go to the browser
in one blob, and the chip re-labels the whole screen without a reload, which a
gettext catalog cannot do without shipping a second copy of itself to JS anyway.

The rule that follows: **no guest-facing string is written in a template or a
script.** If a guest can read it, it is a key in here. English hardcoded in
kiosk.js is exactly how a Bangla kiosk ends up saying "Thinking…" under a Bangla
answer, and "Send" beside a Bangla placeholder.

Adding a key means adding it to both languages. ``tests/integration/
test_kiosk_language.py`` fails if the two sides drift, so a half-translated
addition cannot ship.

The state labels earn their place at the top: they are how a guest knows whether
the machine is hearing them, working, or waiting. Silence with no label reads as
broken, and people tap the button again.
"""

from __future__ import annotations

import json
from typing import Any

BN = "bn"
EN = "en"

#: Placeholder-bearing strings are ``str.format``-ed, never concatenated, so a
#: translator can move the number to wherever the sentence needs it.
CHROME: dict[str, dict[str, Any]] = {
    "en": {
        # --- Conversation state (spoken about, and shown under the avatar) -----
        "ready": "Ready",
        "listening": "Listening… go ahead",
        "thinking": "Thinking…",
        "speaking": "Speaking",
        "offline": "Manual mode — staff will assist",
        "tap": "Tap to speak",
        "armed": "Go ahead, I am listening",
        "hearing": "I can hear you…",
        # Where the microphone opens itself the button is not how you start
        # talking, it is how you stop it — the only label a screen-reader user can
        # act on, and the opposite of "Tap to speak".
        "mic_stop": "Stop the microphone",
        "standby": "Microphone off — tap it to talk again",
        "sending": "Listening — tap again to send",
        "nothing_heard": "I did not hear anything — tap and speak",
        "too_short": "That was too short to hear. Hold the button while you speak.",
        "no_input": "This browser has no speech input — please type",
        "no_record": "This browser cannot record audio — please type",
        "voice_off": "Voice unavailable — please type",
        "placeholder": "Your words will appear here… (or type)",
        "hint": "You can ask me anything about our hotel services.",
        "no_voice": (
            "This terminal has no installed voice for that language, so answers are "
            "shown but not read aloud. Reception can fix it in a minute."
        ),
        "staff_coming": "A member of staff has been notified and is on the way.",
        "staff_notified": "Staff notified.",
        "mic_blocked": "The microphone was blocked",
        "no_reach_mic": "I could not reach the microphone",
        "no_speak": "Nothing can speak on this terminal",
        "unreachable": "Reception is not available right now",
        "desk_unreachable": "Could not reach the desk",
        "sorry": "Sorry",
        "type_instead": "You can type your question instead.",
        # --- The room gallery -------------------------------------------------
        "rooms_some": "Rooms available",
        "rooms_one": "Your room",
        "sleeps": "Sleeps",
        "no_photo": "Photo coming soon",
        # --- Page and panel chrome -------------------------------------------
        "page_title": "AI Reception",
        "brand_sub": "AI Reception",
        "kiosk_title": "AI Reception Kiosk",
        "no_hotel": (
            "No hotel selected. Open this terminal with ?hotel=HOTEL-CODE, "
            "for example /reception/kiosk/?hotel=GLH-001"
        ),
        # --- Controls ---------------------------------------------------------
        "send": "Send",
        "human": "Talk to a human",
        "reset_title": "Start a new guest session",
        "language_label": "Language",
        "voice_label": "Voice",
        "voice_female": "Female",
        "voice_male": "Male",
        "voice_any": "Any",
        "mute_title": "Mute the spoken reply",
        "turns": "Turns",
        # Screen-reader names. A guest using a screen reader on a Bangla kiosk is
        # still a Bangla speaker; an English aria-label is the same bug as an
        # English button, only harder to notice.
        "aria_conversation": "Conversation",
        "aria_message": "Message",
        # --- Booking card -----------------------------------------------------
        "booking_title": "Your booking",
        "booking_draft": "in progress",
        "booking_total": "Total",
        "booking_rows": {
            "check_in": "Arriving",
            "nights": "Nights",
            "room_code": "Room",
            "rooms": "Rooms",
            "adults": "Adults",
            "children": "Children",
            "guest_name": "Name",
            "guest_phone": "Phone",
        },
        "sources": "Sources",
        # The room gallery reads these off the payload's raw values, so the words
        # live here rather than in Django's choice labels: a model's verbose_name
        # is resolved against the request's locale, which is not the language this
        # screen is in.
        "beds": {
            "single": "Single",
            "twin": "Twin",
            "double": "Double",
            "queen": "Queen",
            "king": "King",
            "bunk": "Bunk",
        },
        # A courtesy for the values a seeded property ships with. ``view`` is free
        # text the hotelier owns — anything not in here is shown exactly as they
        # typed it, because guessing at somebody's own words is worse than leaving
        # them alone.
        "views": {
            "sea": "Sea view",
            "city": "City view",
            "garden": "Garden view",
            "pool": "Pool view",
        },
        # Digit glyphs, in order. A Bangla screen where the assistant says
        # "৩১৬২৫ টাকা" while the card beside it says "31625.00" is half switched.
        "digits": "0123456789",
        # --- Header -----------------------------------------------------------
        "model_label": "Model",
        "model_offline": "Offline answers",
        # --- Vision rail ------------------------------------------------------
        "pill_ready": "ready",
        "pill_live": "live",
        "not_enabled": "Not enabled",
        "waiting_guest": "Waiting for a guest",
        "guest_photo_alt": "Guest photo",
        # Booking progress. Four steps, in the order the assistant asks for them,
        # so a guest can see how much is left rather than being asked one more
        # question every time they think they are finished.
        # --- The public booking page's rail -----------------------------------
        "bill_title": "The bill",
        "bill_estimate": "estimate",
        "bill_note": (
            "Priced by the same service the front desk uses, including service "
            "charge and VAT. Nothing is charged here."
        ),
        "slip_title": "Your slip",
        "slip_ready": "confirmed",
        "slip_reference": "Reference",
        "slip_note": (
            "Show this reference at reception. Payment is taken at the desk — this "
            "page has no card reader and never asks for card details."
        ),
        "slip_print": "Print slip",
        "progress_title": "Booking progress",
        "progress_waiting": "Not started",
        "progress_steps": {
            "dates": "Dates",
            "room": "Room",
            "guest": "Your details",
            "confirmed": "Confirmed",
        },
        "storage_encrypted": "Stored encrypted",
        "storage_not_stored": "Consent recorded, images not stored",
        "panels": {
            "face_title": "Guest Photo",
            "face_title_consent": "Guest Photo (on consent)",
            "face_note_on": (
                "After a booking is confirmed, the guest is asked whether we may take "
                "{frames} photos so reception can recognise them on arrival. {storage} "
                "and deleted after {days} days. Declining is recorded too, and changes "
                "nothing about the booking. No automatic matching — a person compares "
                "the photos at the desk."
            ),
            "face_note_off": (
                "Off. The kiosk opens no camera at all: it greets, listens and answers. "
                "Capture needs both the platform flag and this property's flag, plus the "
                "guest saying yes on the consent screen (goal.txt D10, R1)."
            ),
            "ocr_title": "Document OCR",
            "ocr_note": (
                "Passport and NID scanning with MRZ checksum validation arrives in Phase 2."
            ),
            "objects_title": "Object Detection",
            "objects_note": (
                "Luggage detection and bellboy notification. Deferred — not MVP scope."
            ),
            # The rest of the rail. Every one of these is a real product step and
            # none of them is built, so each says so and carries its phase. A
            # mocked-up "verified ✓" here is how a stakeholder comes to believe a
            # compliance feature exists (goal.txt §2.3, D10).
            "recognition_title": "Face Recognition",
            "recognition_note": (
                "Matching an arriving guest against the photos taken at booking. "
                "Nothing is matched automatically today — a person compares them at "
                "the desk."
            ),
            "scan_title": "NID / Passport Scan",
            "scan_note": (
                "The document camera and MRZ read. Phase 2, with the checksum "
                "validation that makes a scan worth trusting."
            ),
            "verify_title": "Document Verification",
            "verify_note": (
                "Cross-checking the scanned document against the booking name and "
                "the guest record. Phase 2, after the scan it depends on."
            ),
            "payment_title": "Payment",
            "payment_note": (
                "Taken at the desk. The assistant can hold a room and quote a price; "
                "it never moves money, and this terminal has no card reader "
                "(goal.txt D11)."
            ),
        },
        # --- Device bar -------------------------------------------------------
        # Set up by staff, read by nobody else — but it sits on the lobby screen
        # in front of the guest, so it is the guest's language like everything
        # else on it.
        "devices": {
            "camera": "Camera",
            "mic": "Microphone",
            "speaker": "Speaker",
            "aria_camera": "Camera",
            "aria_mic": "Microphone input",
            "aria_speaker": "Audio output",
            "default": "{label} — system default",
            "none": "No {label} found",
            "numbered": "{label} {n}",
            "note_labels": "Device names appear once the browser has been allowed access.",
            "note_no_route": (
                "This browser cannot choose an output device — it uses the system default."
            ),
            "note_browser_tts": (
                "The built-in browser voice always plays through the system default output."
            ),
            "note_browser_stt": (
                "Built-in browser speech input always uses the system default microphone."
            ),
        },
        # --- The five tiles ---------------------------------------------------
        # ``prompt`` is what tapping the tile sends as the guest's message, so it
        # is translated too. An English prompt on a Bangla kiosk asks the model a
        # question in the wrong language and gets the answer in it.
        "tiles": [
            {
                "key": "checkin",
                "icon": "⇥",
                "label": "Check-in",
                "sub": "Walk-in / Booking",
                "prompt": "I would like to check in. What do you need from me?",
            },
            {
                "key": "rooms",
                "icon": "🏨",
                "label": "Room Info",
                "sub": "Availability",
                "prompt": "What rooms do you have, and what is included?",
            },
            {
                "key": "services",
                "icon": "🛎",
                "label": "Hotel Services",
                "sub": "Concierge",
                "prompt": "What facilities and services does the hotel offer?",
            },
            {
                "key": "tourist",
                "icon": "🗺",
                "label": "Tourist Guide",
                "sub": "Local Info",
                "prompt": "What is worth seeing near the hotel?",
            },
            {
                "key": "restaurant",
                "icon": "🍽",
                "label": "Restaurant",
                "sub": "Menu & hours",
                "prompt": "What are the restaurant hours, and what is on the menu?",
            },
            {
                "key": "feedback",
                "icon": "💬",
                "label": "Feedback",
                "sub": "& Rating",
                "prompt": "I would like to leave feedback about my stay.",
            },
            {
                "key": "help",
                "icon": "🆘",
                "label": "Help",
                "sub": "Talk to staff",
                "prompt": "I need help from a member of staff, please.",
            },
        ],
    },
    "bn": {
        "ready": "প্রস্তুত",
        "listening": "শুনছি… বলুন",
        "thinking": "ভাবছি…",
        "speaking": "বলছি",
        "offline": "ম্যানুয়াল মোড — কর্মী সাহায্য করবেন",
        "tap": "বলতে চাপ দিন",
        "armed": "বলুন, আমি শুনছি",
        "hearing": "শুনতে পাচ্ছি…",
        "mic_stop": "মাইক বন্ধ করুন",
        "standby": "মাইক বন্ধ — আবার বলতে চাপ দিন",
        "sending": "শুনছি — পাঠাতে আবার চাপ দিন",
        "nothing_heard": "কিছু শুনতে পাইনি — চাপ দিয়ে বলুন",
        "too_short": "খুব ছোট হয়ে গেছে, শোনা যায়নি। বলার সময় বোতাম চেপে ধরুন।",
        "no_input": "এই ব্রাউজারে কথা শোনার সুবিধা নেই — লিখুন",
        "no_record": "এই ব্রাউজার অডিও রেকর্ড করতে পারে না — লিখুন",
        "voice_off": "ভয়েস পাওয়া যাচ্ছে না — লিখুন",
        "placeholder": "এখানে আপনার কথা লেখা উঠবে… (অথবা টাইপ করুন)",
        "hint": "হোটেলের যেকোনো সেবা সম্পর্কে আমাকে জিজ্ঞেস করতে পারেন।",
        "no_voice": (
            "এই টার্মিনালে এই ভাষার ভয়েস ইনস্টল করা নেই, তাই উত্তর লেখা আসছে কিন্তু পড়ে "
            "শোনানো যাচ্ছে না। রিসেপশন এক মিনিটেই ঠিক করে দিতে পারবে।"
        ),
        "staff_coming": "একজন কর্মীকে জানানো হয়েছে, তিনি আসছেন।",
        "staff_notified": "কর্মীকে জানানো হয়েছে।",
        "mic_blocked": "মাইক্রোফোন বন্ধ করা আছে",
        "no_reach_mic": "মাইক্রোফোন পাওয়া গেল না",
        "no_speak": "এই টার্মিনালে কথা বলার ব্যবস্থা নেই",
        "unreachable": "রিসেপশন এই মুহূর্তে পাওয়া যাচ্ছে না",
        "desk_unreachable": "ডেস্কে পৌঁছানো গেল না",
        "sorry": "দুঃখিত",
        "type_instead": "আপনি চাইলে প্রশ্নটি লিখতে পারেন।",
        "rooms_some": "যে রুমগুলো খালি আছে",
        "rooms_one": "আপনার রুম",
        "sleeps": "থাকতে পারবেন",
        "no_photo": "ছবি শিগগিরই আসছে",
        "page_title": "এআই রিসেপশন",
        "brand_sub": "এআই রিসেপশন",
        "kiosk_title": "এআই রিসেপশন কিয়স্ক",
        "no_hotel": (
            "কোনো হোটেল বেছে নেওয়া হয়নি। এই টার্মিনালটি ?hotel=HOTEL-CODE দিয়ে খুলুন, "
            "যেমন /reception/kiosk/?hotel=GLH-001"
        ),
        "send": "পাঠান",
        "human": "একজন মানুষের সাথে কথা বলুন",
        "reset_title": "নতুন অতিথির জন্য শুরু করুন",
        "language_label": "ভাষা",
        "voice_label": "কণ্ঠ",
        "voice_female": "নারী",
        "voice_male": "পুরুষ",
        "voice_any": "যেকোনো",
        "mute_title": "পড়ে শোনানো বন্ধ রাখুন",
        "turns": "কথার পালা",
        "aria_conversation": "কথাবার্তা",
        "aria_message": "বার্তা",
        "booking_title": "আপনার বুকিং",
        "booking_draft": "চলছে",
        "booking_total": "সর্বমোট",
        "booking_rows": {
            "check_in": "আসছেন",
            "nights": "কত রাত",
            "room_code": "রুম",
            "rooms": "কয়টি রুম",
            "adults": "প্রাপ্তবয়স্ক",
            "children": "শিশু",
            "guest_name": "নাম",
            "guest_phone": "মোবাইল",
        },
        "sources": "তথ্যসূত্র",
        "beds": {
            "single": "সিঙ্গেল",
            "twin": "টুইন",
            "double": "ডাবল",
            "queen": "কুইন",
            "king": "কিং",
            "bunk": "বাঙ্ক",
        },
        "views": {
            "sea": "সমুদ্রের দিকে",
            "city": "শহরের দিকে",
            "garden": "বাগানের দিকে",
            "pool": "পুলের দিকে",
        },
        "digits": "০১২৩৪৫৬৭৮৯",
        "model_label": "মডেল",
        "model_offline": "অফলাইন উত্তর",
        "pill_ready": "প্রস্তুত",
        "pill_live": "চলছে",
        "not_enabled": "চালু নেই",
        "waiting_guest": "অতিথির জন্য অপেক্ষা করছি",
        "guest_photo_alt": "অতিথির ছবি",
        "bill_title": "বিল",
        "bill_estimate": "হিসাব",
        "bill_note": (
            "রিসেপশন যে সার্ভিস দিয়ে দাম বের করে, সেটাই এখানে ব্যবহার হয়েছে — সার্ভিস "
            "চার্জ ও ভ্যাট ধরে। এখানে কোনো টাকা নেওয়া হয় না।"
        ),
        "slip_title": "আপনার স্লিপ",
        "slip_ready": "কনফার্ম",
        "slip_reference": "বুকিং নম্বর",
        "slip_note": (
            "রিসেপশনে এই নম্বরটি দেখাবেন। টাকা নেওয়া হয় ডেস্কে — এই পাতায় কার্ড রিডার "
            "নেই এবং কখনো কার্ডের তথ্য চাওয়া হয় না।"
        ),
        "slip_print": "স্লিপ প্রিন্ট করুন",
        "progress_title": "বুকিং কতদূর",
        "progress_waiting": "শুরু হয়নি",
        "progress_steps": {
            "dates": "তারিখ",
            "room": "রুম",
            "guest": "আপনার তথ্য",
            "confirmed": "কনফার্ম",
        },
        "storage_encrypted": "এনক্রিপ্ট করে রাখা হয়",
        "storage_not_stored": "সম্মতি লেখা থাকে, ছবি রাখা হয় না",
        "panels": {
            "face_title": "অতিথির ছবি",
            "face_title_consent": "অতিথির ছবি (সম্মতি নিয়ে)",
            "face_note_on": (
                "বুকিং কনফার্ম হওয়ার পর অতিথিকে জিজ্ঞেস করা হয়, তাঁর {frames} টি ছবি নেওয়া "
                "যাবে কি না — যাতে পৌঁছানোর সময় রিসেপশন তাঁকে চিনতে পারে। {storage} এবং "
                "{days} দিন পর মুছে যায়। না বললে সেটিও লেখা থাকে, বুকিংয়ের কিছুই বদলায় না। "
                "কোনো স্বয়ংক্রিয় মিলিয়ে দেখা নেই — ডেস্কে একজন মানুষ ছবিগুলো মিলিয়ে দেখেন।"
            ),
            "face_note_off": (
                "বন্ধ। কিয়স্ক কোনো ক্যামেরাই খোলে না: শুধু স্বাগত জানায়, শোনে আর উত্তর দেয়। "
                "ছবি নিতে প্ল্যাটফর্ম ও এই হোটেল — দুটোরই অনুমতি লাগে, আর সম্মতির পর্দায় "
                "অতিথির “হ্যাঁ” লাগে (goal.txt D10, R1)।"
            ),
            "ocr_title": "ডকুমেন্ট ওসিআর",
            "ocr_note": ("পাসপোর্ট ও এনআইডি স্ক্যান, সঙ্গে MRZ চেকসাম যাচাই — ফেজ ২-এ আসছে।"),
            "objects_title": "বস্তু শনাক্তকরণ",
            "objects_note": ("লাগেজ শনাক্ত করে বেলবয়কে জানানো। আপাতত স্থগিত — এমভিপির মধ্যে নয়।"),
            "recognition_title": "চেহারা মিলিয়ে দেখা",
            "recognition_note": (
                "বুকিংয়ের সময় নেওয়া ছবির সঙ্গে আসা অতিথিকে মিলিয়ে দেখা। এখন কোনো "
                "স্বয়ংক্রিয় মিল খোঁজা হয় না — ডেস্কে একজন মানুষ ছবিগুলো মিলিয়ে দেখেন।"
            ),
            "scan_title": "এনআইডি / পাসপোর্ট স্ক্যান",
            "scan_note": (
                "ডকুমেন্ট ক্যামেরা আর MRZ পড়া। ফেজ ২-এ আসছে, সঙ্গে সেই চেকসাম যাচাই "
                "যেটা না থাকলে স্ক্যানের উপর ভরসা করা যায় না।"
            ),
            "verify_title": "ডকুমেন্ট যাচাই",
            "verify_note": (
                "স্ক্যান করা ডকুমেন্টের সঙ্গে বুকিংয়ের নাম ও অতিথির রেকর্ড মিলিয়ে দেখা। "
                "ফেজ ২-এ, স্ক্যান চালু হওয়ার পর।"
            ),
            "payment_title": "পেমেন্ট",
            "payment_note": (
                "টাকা নেওয়া হয় ডেস্কে। সহকারী রুম ধরে রাখতে ও দাম বলতে পারে, কিন্তু "
                "কখনো টাকা লেনদেন করে না — আর এই টার্মিনালে কার্ড রিডারও নেই "
                "(goal.txt D11)।"
            ),
        },
        "devices": {
            "camera": "ক্যামেরা",
            "mic": "মাইক্রোফোন",
            "speaker": "স্পিকার",
            "aria_camera": "ক্যামেরা",
            "aria_mic": "মাইক্রোফোন ইনপুট",
            "aria_speaker": "অডিও আউটপুট",
            "default": "{label} — সিস্টেমের ডিফল্ট",
            "none": "কোনো {label} পাওয়া যায়নি",
            "numbered": "{label} {n}",
            "note_labels": "ব্রাউজারকে অনুমতি দেওয়ার পরেই ডিভাইসের নাম দেখা যাবে।",
            "note_no_route": ("এই ব্রাউজার আউটপুট ডিভাইস বেছে নিতে পারে না — সিস্টেমের ডিফল্টই ব্যবহার করবে।"),
            "note_browser_tts": ("ব্রাউজারের নিজের কণ্ঠ সবসময় সিস্টেমের ডিফল্ট আউটপুটেই বাজে।"),
            "note_browser_stt": (
                "ব্রাউজারের নিজের শোনার ব্যবস্থা সবসময় সিস্টেমের ডিফল্ট মাইক্রোফোনই ব্যবহার করে।"
            ),
        },
        "tiles": [
            {
                "key": "checkin",
                "icon": "⇥",
                "label": "চেক-ইন",
                "sub": "ওয়াক-ইন / বুকিং",
                "prompt": "আমি চেক-ইন করতে চাই। আমার কাছ থেকে কী কী লাগবে?",
            },
            {
                "key": "rooms",
                "icon": "🏨",
                "label": "রুমের তথ্য",
                "sub": "কী কী খালি আছে",
                "prompt": "আপনাদের কী কী রুম আছে, আর ভাড়ার সাথে কী কী পাওয়া যায়?",
            },
            {
                "key": "services",
                "icon": "🛎",
                "label": "হোটেলের সেবা",
                "sub": "কনসিয়ার্জ",
                "prompt": "হোটেলে কী কী সুবিধা ও সেবা আছে?",
            },
            {
                "key": "tourist",
                "icon": "🗺",
                "label": "ঘোরার জায়গা",
                "sub": "আশেপাশের তথ্য",
                "prompt": "হোটেলের আশেপাশে দেখার মতো কী কী আছে?",
            },
            {
                "key": "restaurant",
                "icon": "🍽",
                "label": "রেস্টুরেন্ট",
                "sub": "মেনু ও সময়",
                "prompt": "রেস্টুরেন্ট কখন খোলা থাকে, আর মেনুতে কী কী আছে?",
            },
            {
                "key": "feedback",
                "icon": "💬",
                "label": "মতামত",
                "sub": "ও রেটিং",
                "prompt": "আমি এখানে থাকার অভিজ্ঞতা নিয়ে মতামত দিতে চাই।",
            },
            {
                "key": "help",
                "icon": "🆘",
                "label": "সাহায্য",
                "sub": "কর্মীর সাথে কথা",
                "prompt": "আমার একজন কর্মীর সাহায্য দরকার।",
            },
        ],
    },
}


#: Consent copy, kept beside the rest of the guest's words rather than in the
#: template, so a translator changes one file and so ``CONSENT_TEXT_VERSION`` in
#: ``services.vision.enrolment`` has something concrete to version.
ENROL: dict[str, dict[str, Any]] = {
    "en": {
        "title": "Shall we take a photo to speed up your check-in?",
        "body": (
            "Your booking is confirmed. If you would like, we can take a few photos now "
            "so our reception can recognise you quickly when you arrive."
        ),
        "bullets": [
            "{frames} photos of your face, nothing else.",
            "Stored encrypted, and deleted automatically after {days} days.",
            "Saying no changes nothing about your booking or your check-in.",
        ],
        "accept": "Yes, take the photos",
        "decline": "No, thank you",
        # What counts as yes or no when the guest answers out loud. Server-side so
        # a translator owns them alongside the question, and matched as substrings
        # because speech recognition returns "yes please" and "no thanks, I am in a
        # hurry", never a bare token.
        "yes_words": [
            "yes",
            "yeah",
            "yep",
            "ok",
            "okay",
            "sure",
            "go ahead",
            "please do",
            "fine",
            "alright",
            "do it",
        ],
        "no_words": [
            "no",
            "nope",
            "not now",
            "skip",
            "later",
            "don't",
            "do not",
            "no thanks",
            "rather not",
        ],
        "cancel": "Stop and delete",
        "capturing": "Photo {n} of {total}",
        "done": "Thank you — all done.",
        "failed": "We could not take the photos. Reception will help you at the desk.",
        "camera_blocked": "The camera is not available. Nothing was taken.",
        "poses": [
            "Look straight at the screen",
            "Look straight at the screen",
            "Turn slightly to your left",
            "Turn slightly to your right",
            "Chin up a little",
            "A small smile",
        ],
    },
    "bn": {
        "title": "চেক-ইন দ্রুত করতে কয়েকটি ছবি নেব?",
        "body": (
            "আপনার বুকিং কনফার্ম হয়েছে। আপনি চাইলে এখন কয়েকটি ছবি নিতে পারি, "
            "যাতে পৌঁছানোর সময় রিসেপশনে আপনাকে দ্রুত চিনে নেওয়া যায়।"
        ),
        "bullets": [
            "শুধু আপনার মুখের {frames} টি ছবি, আর কিছু নয়।",
            "ছবি এনক্রিপ্ট করে রাখা হয় এবং {days} দিন পর নিজে থেকেই মুছে যায়।",
            "না বললেও আপনার বুকিং বা চেক-ইনে কোনো অসুবিধা হবে না।",
        ],
        "accept": "ঠিক আছে, ছবি নিন",
        "decline": "না, দরকার নেই",
        "yes_words": [
            "হ্যাঁ",
            "হ্যা",
            "জি",
            "জী",
            "ঠিক আছে",
            "আচ্ছা",
            "অবশ্যই",
            "নিন",
            "নাও",
            "করুন",
            "রাজি",
            "সম্মত",
        ],
        "no_words": [
            "না",
            "নাহ",
            "দরকার নেই",
            "লাগবে না",
            "থাক",
            "চাই না",
            "পরে",
            "বাদ দিন",
            "করব না",
        ],
        "cancel": "বন্ধ করুন ও মুছে ফেলুন",
        "capturing": "{total} টির মধ্যে {n} নম্বর ছবি",
        "done": "ধন্যবাদ — হয়ে গেছে।",
        "failed": "ছবি নেওয়া গেল না। রিসেপশনে আমাদের কর্মী সাহায্য করবেন।",
        "camera_blocked": "ক্যামেরা পাওয়া যাচ্ছে না। কোনো ছবি নেওয়া হয়নি।",
        "poses": [
            "সোজা স্ক্রিনের দিকে তাকান",
            "সোজা স্ক্রিনের দিকে তাকান",
            "একটু বাঁ দিকে ঘুরুন",
            "একটু ডান দিকে ঘুরুন",
            "থুতনি একটু উপরে",
            "একটু হাসুন",
        ],
    },
}


def resolve(language: str | None) -> str:
    """Which of the two we have. Anything not Bangla is English.

    Takes ``bn``, ``bn-BD``, ``BN``, ``None`` — the language arrives from a
    property setting, a browser speech tag and a model's own answer, and all
    three spell it differently.
    """
    return BN if str(language or EN).lower().startswith(BN) else EN


def chrome(language: str | None) -> dict[str, Any]:
    """The chrome dictionary for one language."""
    return CHROME[resolve(language)]


def enrol(language: str | None) -> dict[str, Any]:
    return ENROL[resolve(language)]


def chrome_json() -> str:
    """Both languages, for the browser.

    Both rather than one, because the guest switches language by tapping a chip
    and the whole screen has to follow inside the same second. Fetching the other
    half at that moment would mean a visible English flash, or a spinner, on a
    screen whose entire job is to feel like talking to somebody.

    It is ~6 KB of JSON on a page that already carries a room photograph.
    """
    return json.dumps(CHROME, ensure_ascii=False)
