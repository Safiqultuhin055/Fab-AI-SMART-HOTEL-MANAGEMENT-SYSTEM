"""The lobby terminal's layout: header, conversation, quick actions, rail.

A 1920×1080 wall panel, one screen, nothing scrolling except the conversation.
The rules being pinned here are the ones that were wrong first:

  the header       one bar, at the very top, carrying everything that identifies
                   the machine. There were two — a page topline and the widget's
                   own title bar — with 18px of nothing above them.
  the conversation the tallest thing on the screen, and the thing that grows when
                   the screen does.
  quick actions    a finger's target, seven of them, and nothing floating on top.
  the rail         the whole arrival, honestly labelled: two steps built, five
                   named with their phase and no fabricated verdict.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.urls import reverse

from apps.reception.copy import CHROME

pytestmark = [pytest.mark.django_db, pytest.mark.integration]

CSS = Path(__file__).parents[2] / "static" / "css" / "kiosk.css"
KIOSK_JS = Path(__file__).parents[2] / "static" / "js" / "kiosk.js"
PANEL = Path(__file__).parents[2] / "templates" / "reception" / "_kiosk_panel.html"


def terminal(client, hotel) -> str:
    return client.get(f"{reverse('reception:kiosk')}?hotel={hotel.code}").content.decode()


class TestTheHeader:
    def test_one_bar_with_everything_that_identifies_the_machine(self, client, hotel):
        body = terminal(client, hotel)

        assert 'class="kiosk-header"' in body
        # Hotel, devices, model, clock — in that order across the bar.
        for anchor in (
            "kiosk-header__brand",
            'id="kiosk-devices"',
            "kiosk-header__model",
            'id="kiosk-time"',
            'id="kiosk-date"',
        ):
            assert anchor in body, anchor

    def test_the_second_header_bar_is_gone_from_the_terminal(self, client, hotel):
        """Two title bars above one conversation is 55px of nothing. The widget's
        own header is for the staff console, where the widget is embedded in a
        page that has its own topbar."""
        assert "kiosk-stage__header" not in terminal(client, hotel)

    def test_the_console_keeps_its_own(self):
        """The panel is shared. The console embeds it in a page that has its own
        topbar, so the widget keeps its title bar there — the terminal is the only
        place where it was a duplicate. (The console's own render is covered in
        test_kiosk_scene.py, which does the role setup.)"""
        panel = PANEL.read_text(encoding="utf-8")

        # Console only now, by name: the terminal and the public booking page both
        # have a page header, and only the console embeds the widget in a page whose
        # topbar belongs to something else.
        assert "{% if channel == 'web' %}" in panel
        assert "kiosk-stage__header" in panel
        assert 'id="kiosk-title"' in panel

    def test_nothing_sits_above_it(self):
        """18px of shell padding above the header reads as a page that failed to
        load its top bar."""
        css = CSS.read_text(encoding="utf-8")
        block = css[css.index(".kiosk-fullscreen {") : css.index(".kiosk-header {")]
        assert "padding: 0 16px 14px" in block

    def test_the_model_is_named_but_never_its_key(self, client, hotel):
        """A staff member setting a terminal up should see what it is talking to
        without opening AI Center. A key on a lobby screen is a key on a lobby
        screen."""
        body = terminal(client, hotel)
        assert "kiosk-header__model" in body
        assert "api_key" not in body
        # A key shape, not the two letters "sk" — "kiosk-mode" contains those.
        assert not re.search(r"sk-[A-Za-z0-9]{16,}", body)


class TestQuickActions:
    def test_seven_of_them_and_every_one_is_a_real_prompt(self, client, hotel):
        """Tapping a tile sends its prompt as the guest's own message, so a guest
        who taps travels the same answer path as one who talks. Nothing here is a
        shortcut around the assistant."""
        body = terminal(client, hotel)
        keys = re.findall(r'data-tile="([^"]+)"', body)

        assert keys == ["checkin", "rooms", "services", "tourist", "restaurant", "feedback", "help"]
        assert body.count("data-prompt=") == 7

    def test_they_are_a_fingers_target_not_a_mouses(self):
        css = CSS.read_text(encoding="utf-8")
        block = css[css.index(".kiosk-mode .tile {") : css.index(".kiosk-mode .tile:hover")]
        # Below about 80px a finger starts hitting two of them.
        assert "min-height: 96px" in block

    def test_the_press_is_visible(self):
        """On a touch screen :hover is whatever was tapped last, so the state that
        confirms the tap is :active."""
        css = CSS.read_text(encoding="utf-8")
        assert ".kiosk-mode .tile:hover" in css
        assert ".kiosk-mode .tile:active" in css
        assert "scale(0.97)" in css

    def test_the_grid_fits_all_seven_on_one_row(self):
        css = CSS.read_text(encoding="utf-8")
        block = css[css.index(".kiosk-tiles {") :][:260]
        assert "repeat(7, minmax(0, 1fr))" in block


class TestBookingProgress:
    def test_four_steps_in_the_order_they_are_asked_for(self, client, hotel):
        body = terminal(client, hotel)
        steps = re.findall(r'data-step="([^"]+)"', body)

        assert steps == ["dates", "room", "guest", "confirmed"]
        for label in CHROME["en"]["progress_steps"].values():
            assert label in body, label

    def test_it_reads_the_validated_draft_not_the_assistants_word(self):
        """The assistant claiming a booking is confirmed does not make it so: the
        confirmed step lights up on booking.code, which only exists once a
        reservation has been written."""
        source = KIOSK_JS.read_text(encoding="utf-8")
        block = source[source.index("const renderProgress") : source.index("const renderBooking")]

        assert "confirmed: Boolean(booking && booking.code)" in block
        assert "dates: Boolean(booking && booking.check_in && booking.nights)" in block
        # And the step being asked about right now is marked, so the guest's eye
        # lands on it rather than on the four they already answered.
        assert "is-current" in block

    def test_the_steps_are_in_the_guests_language(self, client, hotel):
        hotel.kiosk_language = "bn"
        hotel.save(update_fields=["kiosk_language"])
        body = terminal(client, hotel)

        for label in CHROME["bn"]["progress_steps"].values():
            assert label in body, label


class TestTheRail:
    def test_the_whole_arrival_is_listed(self, client, hotel):
        """A guest should be able to see what this machine will and will not do
        with their face and their passport."""
        body = terminal(client, hotel)
        words = CHROME["en"]["panels"]

        for key in ("face_title", "recognition_title", "scan_title", "ocr_title", "verify_title"):
            assert words[key] in body, key
        assert words["payment_title"] in body

    def test_the_five_unbuilt_ones_say_so_and_carry_their_phase(self, client, hotel):
        body = terminal(client, hotel)

        assert body.count("Not enabled") >= 4
        assert body.count(">P2<") >= 3
        assert body.count(">P3<") >= 1

    def test_no_card_claims_a_result_it_does_not_have(self, client, hotel):
        """A mocked-up "verified ✓" is how a stakeholder comes to believe a
        compliance feature exists — and how a guest comes to believe their
        passport was checked (goal.txt §2.3, D10)."""
        body = terminal(client, hotel)

        for claim in ("Verified", "Matched", "Scanned ✓", "Paid"):
            assert claim not in body, claim

    def test_payment_says_where_money_is_actually_taken(self, client, hotel):
        """goal.txt D11: the assistant may hold a room, never move money. A
        payment card that implies otherwise on the screen is the same promise
        broken in a different place."""
        body = terminal(client, hotel)
        assert "Taken at the desk" in body
        assert "card reader" in body

    def test_the_column_is_still_320px(self):
        """The brief said the sidebar's width does not change."""
        css = CSS.read_text(encoding="utf-8")
        block = css[css.index(".kiosk {") : css.index(".kiosk-stage {")]
        assert "320px" in block
