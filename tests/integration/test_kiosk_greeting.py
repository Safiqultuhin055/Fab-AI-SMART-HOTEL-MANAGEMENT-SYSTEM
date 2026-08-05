"""The kiosk's front door: the assistant greets, and nothing watches.

The entry screen used to be a camera. It is now the assistant — it greets,
listens and answers before anything is pointed at the guest. These tests pin
that, because "the terminal photographs you before it says hello" is the kind of
regression that arrives quietly in a template.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.reception.models import GreetingStyle
from services.reception import orchestrator

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


def kiosk_html(client, hotel):
    return client.get(f"{reverse('reception:kiosk')}?hotel={hotel.code}").content.decode()


class TestGreeting:
    def test_is_time_aware(self, hotel):
        """ "Hello" is what a machine says; "Good evening" is what a receptionist says."""
        text = orchestrator.greeting(hotel)
        hour = timezone.localtime().hour
        expected = "morning" if hour < 12 else "afternoon" if hour < 17 else "evening"
        assert expected in text.lower()

    def test_names_the_hotel_and_offers_help(self, hotel):
        text = orchestrator.greeting(hotel)
        assert hotel.name in text
        assert "assist" in text.lower()

    def test_welcomes_a_returning_guest_by_name(self, hotel):
        assert "Rina" in orchestrator.greeting(hotel, guest_name="Rina")
        assert "back" in orchestrator.greeting(hotel, guest_name="Rina").lower()

    def test_default_greeting_is_not_religious(self, hotel):
        """A religious greeting to every guest is the hotel's decision, not ours."""
        assert "alaikum" not in orchestrator.greeting(hotel).lower()

    def test_islamic_style_is_available_when_the_hotel_chooses_it(self, hotel):
        hotel.kiosk_greeting_style = GreetingStyle.ISLAMIC
        hotel.save()
        assert "Assalamu alaikum" in orchestrator.greeting(hotel)

    def test_formal_style(self, hotel):
        hotel.kiosk_greeting_style = GreetingStyle.FORMAL
        hotel.save()
        text = orchestrator.greeting(hotel)
        assert "welcome," not in text.lower()

    def test_the_start_endpoint_asks_the_language_before_it_welcomes_anybody(self, client, hotel):
        """Greeting first would mean guessing a language, and the welcome is the
        one sentence you least want to get wrong."""
        response = client.post(
            reverse("v1:reception_start"),
            {"channel": "kiosk"},
            content_type="application/json",
            HTTP_X_HOTEL_CODE=hotel.code,
        )
        assert response.status_code == 201
        body = response.json()
        assert body["greeting"] == ""
        assert len(body["language_prompt"]) == 2

    def test_the_welcome_arrives_with_the_answer_to_that_question(self, client, hotel):
        start = client.post(
            reverse("v1:reception_start"),
            {"channel": "kiosk"},
            content_type="application/json",
            HTTP_X_HOTEL_CODE=hotel.code,
        )
        reply = client.post(
            reverse("v1:reception_chat"),
            {"conversation": start.json()["conversation"], "message": "English"},
            content_type="application/json",
            HTTP_X_HOTEL_CODE=hotel.code,
        )
        assert hotel.name in reply.json()["reply"]


class TestTheAssistantIsTheFrontDoor:
    def test_the_conversation_is_on_screen_immediately(self, client, hotel):
        body = kiosk_html(client, hotel)
        marker = body.index('id="assistant-stage"')
        assert "is-hidden" not in body[marker : marker + 80]

    def test_the_microphone_is_there_from_the_start(self, client, hotel):
        """Audio assistant first — the guest can talk before anything else."""
        body = kiosk_html(client, hotel)
        assert 'id="kiosk-mic"' in body
        assert 'id="kiosk-wave"' in body
        assert 'id="kiosk-input"' in body

    def test_no_camera_gate_stands_between_the_guest_and_the_assistant(self, client, hotel):
        """The old two-stage flow opened a webcam on page load. It must not
        come back: the capture overlay ships hidden, and nothing auto-starts it."""
        body = kiosk_html(client, hotel)

        marker = body.index('id="enrol-stage"')
        assert "is-hidden" in body[body.rindex("<section", 0, marker) : marker + 120]
        assert "kiosk-face.js" not in body  # the retired camera-first gate

    def test_only_one_video_element_exists(self, client, hotel):
        """Two <video> tags sharing an id silently breaks the capture."""
        assert kiosk_html(client, hotel).count('id="kiosk-cam"') == 1

    def test_the_rail_never_shows_a_live_feed(self, client, hotel):
        body = kiosk_html(client, hotel)
        assert 'class="vision-cam"' not in body
