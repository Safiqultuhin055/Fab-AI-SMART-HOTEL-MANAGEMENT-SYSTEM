"""The lobby scene, and the line it does not cross.

A restyle is the easiest place to break working software: the JS hooks are ids in
a template nobody thinks of as code. Most of these tests exist to prove the
redesign kept every one of them.

The other half is about the figure. The reference design has a photorealistic
receptionist, and shipping one would mean bundling a stock face — a person who
does not exist, whose likeness we hold no licence for, presented to guests as
staff. So the portrait is an upload the property makes, and the fallback is
honest about being software.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

pytestmark = [pytest.mark.django_db, pytest.mark.integration]

CSS = Path(__file__).parents[2] / "static" / "css" / "kiosk.css"
KIOSK_JS = Path(__file__).parents[2] / "static" / "js" / "kiosk.js"

# A tiny PNG. Never decoded — this is not an image processing test.
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c6300010000050001"
    "0d0a2db40000000049454e44ae426082"
)


def kiosk(client, hotel):
    return client.get(f"{reverse('reception:kiosk')}?hotel={hotel.code}").content.decode()


class TestEveryHookSurvived:
    """Ids the client reaches for. A restyle that drops one takes a feature with
    it, silently."""

    @pytest.mark.parametrize(
        "anchor",
        [
            "kiosk",
            "kiosk-avatar",
            "kiosk-state",
            "kiosk-bubbles",
            "kiosk-wave",
            "kiosk-mic",
            "kiosk-mic-hint",
            "kiosk-mute",
            "kiosk-turns",
            "kiosk-form",
            "kiosk-input",
            "kiosk-human",
            "kiosk-reset",
            "kiosk-devices",
            "kiosk-booking",
        ],
    )
    def test_the_id_is_still_there(self, client, hotel, anchor):
        assert f'id="{anchor}"' in kiosk(client, hotel)

    def test_the_action_tiles_still_carry_real_prompts(self, client, hotel):
        assert kiosk(client, hotel).count("data-prompt=") >= 5

    @pytest.mark.parametrize(
        "attr",
        [
            "data-ai-state",
            "data-voice",
            "data-tts",
            "data-language",
            "data-speech-lang",
            "data-copy",
            "data-hands-free",
            "data-voice-gender",
            "data-hotel",
        ],
    )
    def test_the_data_attributes_the_client_reads_are_intact(self, client, hotel, attr):
        assert f"{attr}=" in kiosk(client, hotel)

    def test_the_avatar_still_carries_the_state_machine(self, client, hotel):
        """setState writes data-state on #kiosk-avatar; the orb and the mic ring
        both key off it."""
        body = kiosk(client, hotel)
        marker = body.index('id="kiosk-avatar"')
        assert 'data-state="idle"' in body[marker - 80 : marker + 80]


class TestTheFigure:
    def test_the_default_figure_is_drawn_not_photographed(self, client, hotel):
        """A stock face means presenting a person who does not exist as staff,
        using a likeness nobody licensed to us. So it is illustrated, inline, and
        described as an illustration to assistive tech."""
        body = kiosk(client, hotel)
        assert 'class="figure"' in body
        assert "<svg" in body
        assert "Illustrated AI receptionist" in body
        assert "scene__portrait" not in body

    def test_the_default_figure_is_animated(self, client, hotel):
        """A still figure reads as a poster, and nobody talks to a poster."""
        body = kiosk(client, hotel)
        for part in ("figure__body", "figure__head", "figure__lid", "figure__mouth-open"):
            assert part in body, part

        css = CSS.read_text(encoding="utf-8")
        for animation in ("figure-breathe", "figure-blink", "figure-sway", "figure-talk"):
            assert f"@keyframes {animation}" in css, animation

    def test_the_figure_follows_the_same_state_machine(self, client, hotel):
        """No JavaScript of its own: setState already writes data-state on
        #kiosk-avatar, and the CSS keys off that."""
        css = CSS.read_text(encoding="utf-8")
        for state in ("listening", "thinking", "speaking"):
            assert f'[data-state="{state}"] .figure' in css, state

    def test_the_mouth_only_moves_while_an_answer_is_spoken(self, client, hotel):
        """Movement that means nothing is worse than no movement."""
        css = CSS.read_text(encoding="utf-8")
        closed = css[css.index(".figure__mouth-open {") : css.index("/* --- State: listening")]
        assert "scaleY(0)" in closed
        assert "opacity: 0" in closed
        assert '[data-state="speaking"] .figure__mouth-open' in css

    def test_reduced_motion_leaves_a_complete_figure_not_a_broken_one(self):
        """Eyes open, mouth shut — a stilled lid mid-blink would be a face with
        its eyes closed."""
        css = CSS.read_text(encoding="utf-8")
        block = css[css.rindex("prefers-reduced-motion: reduce") :]
        assert "figure__lid { transform: scaleY(0); }" in block
        assert "figure__mouth-open { opacity: 0; }" in block

    def test_the_property_can_supply_its_own_portrait(self, client, hotel):
        hotel.kiosk_avatar = SimpleUploadedFile("receptionist.png", PNG, content_type="image/png")
        hotel.save(update_fields=["kiosk_avatar"])

        body = kiosk(client, hotel)
        assert "scene__portrait" in body
        assert "receptionist" in body
        # The drawn figure steps aside rather than stacking behind the photograph.
        assert 'class="figure"' not in body

    def test_the_portrait_is_hidden_from_assistive_tech(self, client, hotel):
        """It carries no information a screen reader needs; the state does."""
        hotel.kiosk_avatar = SimpleUploadedFile("r.png", PNG, content_type="image/png")
        hotel.save(update_fields=["kiosk_avatar"])

        body = kiosk(client, hotel)
        marker = body.index("scene__portrait")
        chunk = body[marker : marker + 220]
        assert 'alt=""' in chunk
        assert "aria-hidden" in chunk

    def test_a_backdrop_is_dimmed_rather_than_shown_raw(self, client, hotel):
        """White text over an un-art-directed lobby photo is unreadable."""
        hotel.kiosk_backdrop = SimpleUploadedFile("lobby.png", PNG, content_type="image/png")
        hotel.save(update_fields=["kiosk_backdrop"])

        assert "has-backdrop" in kiosk(client, hotel)

        css = CSS.read_text(encoding="utf-8")
        block = css[css.index(".scene.has-backdrop::before") : css.index(".scene__chip {")]
        assert "blur(" in block
        assert "brightness(" in block


class TestSceneChrome:
    def test_the_guest_is_not_shown_the_provider_s_health(self, client, hotel):
        """There was an "AI Assistant / AI failing — answering from hotel data"
        badge on the scene. It is gone.

        Whether the provider is up, degraded or unconfigured is an operator's
        problem, and the operator has it in the console topbar, the dashboard and
        AI Center. A guest in a lobby cannot act on it, and telling them the
        machine is failing while it answers them perfectly well from the hotel
        record undermines the answer they just got.
        """
        body = kiosk(client, hotel)

        assert "scene__chip--status" not in body
        assert "AI Assistant" not in body
        assert 'id="kiosk-ai-label"' not in body

    def test_the_state_itself_still_reaches_the_script(self, client, hotel):
        """Removing the badge must not remove the fact: kiosk.js reads
        data-ai-state to decide whether the microphone and the answer path are
        live."""
        assert 'data-ai-state="' in kiosk(client, hotel)

    def test_the_hint_line_is_in_the_guests_language(self, client, hotel):
        hotel.kiosk_language = "bn"
        hotel.save(update_fields=["kiosk_language"])
        assert "হোটেলের যেকোনো সেবা" in kiosk(client, hotel)

    def test_a_property_can_write_its_own_hint(self, client, hotel):
        hotel.kiosk_hint = "Ask me about the rooftop pool."
        hotel.save(update_fields=["kiosk_hint"])
        assert "Ask me about the rooftop pool." in kiosk(client, hotel)

    def test_the_voice_control_actually_changes_the_voice(self, client, hotel):
        """In the reference it is a chip. A chip that does nothing is worse than no
        chip at all."""
        assert 'id="kiosk-voice-pick"' in kiosk(client, hotel)

        source = KIOSK_JS.read_text(encoding="utf-8")
        assert "el.voicePick.addEventListener('change'" in source
        # Remembered per terminal, so the next guest is not reset to the row value.
        assert "localStorage.setItem(voiceKey" in source
        # Audible immediately, which is the only confirmation the control offers.
        assert "if (lastGreeting) speak(lastGreeting);" in source

    def test_the_waveform_reads_as_one_instrument(self):
        """48 independently coloured bars look like 48 lights, not one strip."""
        css = CSS.read_text(encoding="utf-8")
        block = css[css.index(".waveform__bar {") : css.index(".waveform.is-live")]
        assert "background-attachment: fixed" in block

    def test_the_hint_does_not_move_the_mic_button(self):
        """It changes on every state; a line that appears and disappears makes the
        button jump under the guest's finger."""
        css = CSS.read_text(encoding="utf-8")
        start = css.index(".mic-hint {")
        assert "min-height" in css[start : start + 400]

    def test_the_figure_yields_to_the_conversation_on_a_narrow_screen(self):
        """The portrait is decoration; the conversation is the product."""
        css = CSS.read_text(encoding="utf-8")
        block = css[css.index("@media (max-width: 900px)") :]
        assert ".scene__figure" in block
        assert "opacity" in block


class TestTheConsoleStillWorks:
    def test_the_same_panel_renders_for_staff(self, client, hotel, receptionist):
        """One implementation, two pages. A scene that only worked full-screen
        would have quietly broken the console."""
        from django.core.management import call_command

        from apps.accounts.backends import invalidate_permission_cache
        from apps.accounts.models import Role, RoleCode
        from apps.core.context import set_request_context
        from apps.tenants.models import HotelMembership

        call_command("seed_roles", stdout=io.StringIO())
        HotelMembership.objects.filter(user=receptionist).delete()
        HotelMembership.objects.create(
            user=receptionist,
            hotel=hotel,
            role=Role.objects.get(code=RoleCode.AI_RECEPTION),
            is_default=True,
        )
        set_request_context(tenant_id=str(hotel.pk))
        invalidate_permission_cache(str(receptionist.pk), str(hotel.pk))

        client.force_login(receptionist)
        body = client.get(reverse("reception:home")).content.decode()

        assert 'class="scene' in body
        assert 'id="kiosk-mic"' in body
        # ...but never a standing microphone on a receptionist's own laptop.
        assert 'data-hands-free="false"' in body


class TestTheRoomPhotoRidesWithTheAnswer:
    """The gallery on the right is a reference the guest has to look away to
    read. The photograph belongs in the message that talks about the room — which
    is what a receptionist does when they turn the screen round.
    """

    def test_the_bubble_can_carry_a_photo_above_its_text(self):
        source = KIOSK_JS.read_text(encoding="utf-8")
        # The citations argument is gone with the footer it drew; the photo is what a
        # bubble still carries besides its words.
        assert "const addBubble = (who, text, photo) =>" in source
        # Above the words, not below: a picture under three lines of Bangla
        # arrives after the guest has finished reading and looked away.
        assert "node.insertBefore(image, node.firstChild)" in source

    def test_the_answer_gets_the_photo_of_the_room_being_taken(self):
        source = KIOSK_JS.read_text(encoding="utf-8")
        assert "addBubble('ai', data.reply, bubblePhoto(data.booking))" in source
        # Only the chosen room. While the guest is still choosing, four options
        # belong in the gallery; a bubble can honestly carry one.
        assert "gallery.find((entry) => entry.chosen)" in source

    def test_the_same_room_is_not_pictured_on_every_following_turn(self):
        """Name, phone, "confirm?" — three more answers about a room already
        shown. Repeating it down the transcript reads as a bug."""
        source = KIOSK_JS.read_text(encoding="utf-8")
        assert "if (room.code === picturedRoom) return null;" in source
        # ...and the next guest starts from nothing.
        assert source.count("picturedRoom = ''") >= 2

    def test_the_photo_cannot_push_the_words_off_a_short_screen(self):
        css = CSS.read_text(encoding="utf-8")
        block = css[css.index(".bubble__photo {") :][:400]
        assert "max-height" in block
        assert "object-fit: cover" in block


class TestTheGalleryLooksLikeThePage:
    """The room gallery is a panel on the AI Reception screen, not a widget that
    landed on it. Same glass, same title style, same baseline as the chip beside
    it — every one of those was wrong first, and each one is visible from across
    a lobby.
    """

    def test_the_title_reuses_the_page_s_panel_title(self, client, hotel):
        """A second definition of "panel title" is how two of them drift apart."""
        body = kiosk(client, hotel)
        assert 'class="card-title-ashos mb-0" id="kiosk-rooms-title"' in body

    def test_the_panel_shares_the_glass_and_the_baseline(self):
        css = CSS.read_text(encoding="utf-8")
        block = css[css.index(".scene__rooms {") : css.index(".scene__rooms.is-hidden")]
        # Same 16px line the status chip starts on, in the same glass the chips use.
        assert "top: 16px" in block
        assert "backdrop-filter" in block
        # Hugs its content like the cards on the rail, rather than stretching the
        # full height of the scene with one card in it.
        assert "max-height" in block
        assert "bottom:" not in block

    def test_a_card_never_shrinks_to_fit(self):
        """Four cards in a flex column short of space squeezed every one of them,
        and overflow:hidden then ate the name and the facts under the photo —
        four unlabelled photographs. The list scrolls instead."""
        css = CSS.read_text(encoding="utf-8")
        block = css[css.index(".room-card {") : css.index(".room-card.is-chosen")]
        assert "flex: 0 0 auto" in block

        list_block = css[css.index(".rooms__list {") :][:300]
        assert "overflow-y: auto" in list_block

    def test_the_conversation_is_kept_clear_of_the_panel(self):
        """216 wide, 16 from the edge, 12 of air between. A bubble is 82% of the
        panel and would otherwise run underneath it."""
        css = CSS.read_text(encoding="utf-8")
        assert "padding-right: 244px" in css


class TestTheConversationOwnsTheHeight:
    """The chat was a 248px window in the middle of a 1080p lobby panel, and the
    newest answer was clipped by its bottom edge — the one message that matters.
    """

    def test_the_scroller_takes_the_space_instead_of_a_fraction_of_it(self):
        css = CSS.read_text(encoding="utf-8")
        block = css[css.index(".kiosk-bubbles {") : css.index(".kiosk-bubbles > :first-child")]

        assert "flex: 1 1 auto" in block
        assert "max-height: none" in block
        # The one that actually makes a flex child scroll: without it the item
        # refuses to shrink below its content and the overflow lands on the page.
        assert "min-height: 0" in block

    def test_the_conversation_grows_from_the_bottom(self):
        """Two bubbles sit at the bottom of the panel, not stranded at the top of
        a tall empty box."""
        css = CSS.read_text(encoding="utf-8")
        assert ".kiosk-bubbles > :first-child { margin-top: auto; }" in css
        # justify-content:flex-end would do it too, and makes overflowing content
        # unreachable above the scroll origin in Firefox.
        block = css[css.index(".kiosk-bubbles {") : css.index(".kiosk-bubbles > :first-child")]
        assert "justify-content: flex-end" not in block

    def test_the_height_is_handed_down_every_step_of_the_chain(self):
        """Four elements between the viewport and the scene. One of them standing
        at its content height pushes the composer off the bottom of the screen,
        which is exactly what happened: #assistant-stage is a plain div."""
        css = CSS.read_text(encoding="utf-8")

        for selector in (
            ".kiosk-fullscreen #assistant-stage",
            ".kiosk-mode .kiosk {",
            ".kiosk-mode .kiosk-stage {",
            ".kiosk-mode .scene {",
        ):
            assert selector in css, selector
        # Exactly the viewport, not "at least" it.
        fullscreen = css[css.index(".kiosk-fullscreen {") : css.index(".kiosk-topline")]
        assert "height: 100vh" in fullscreen

    def test_the_newest_turn_is_pinned_three_ways(self):
        """Assigning scrollTop once, straight after appendChild, scrolls to the
        height the box had BEFORE the new bubble was laid out — a line or two
        short, which is how the last answer sat half-cut at the bottom edge."""
        source = KIOSK_JS.read_text(encoding="utf-8")
        block = source[source.index("const scrollToLatest") : source.index("if (el.bubbles) {")]

        assert "pinToBottom();" in block
        assert "requestAnimationFrame(pinToBottom)" in block
        # rAF does not fire in a background tab, and a terminal left on another tab
        # and switched back is an ordinary Tuesday.
        assert "setTimeout(pinToBottom, 0)" in block

    def test_a_photo_re_pins_when_it_finishes_decoding(self):
        """An image has no height until it decodes, so the scroll that ran on
        append was short by the height of the picture."""
        source = KIOSK_JS.read_text(encoding="utf-8")
        assert "image.addEventListener('load', scrollToLatest)" in source

    def test_a_guest_reading_back_is_not_yanked_to_the_bottom(self):
        """...but a new turn wins, because that is what they asked the machine
        for: every addBubble() calls scrollToLatest(), which re-sticks."""
        source = KIOSK_JS.read_text(encoding="utf-8")

        assert "let stuckToBottom = true;" in source
        assert "stuckToBottom = distance < 40;" in source
        assert "if (stuckToBottom) el.bubbles.scrollTop" in source
