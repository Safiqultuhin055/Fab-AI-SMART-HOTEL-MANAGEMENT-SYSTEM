"""Rendered-output guards.

A template that renders its own source is invisible to unit tests of views and
services — the response is still HTTP 200. Only asserting on the rendered body
catches it, which is why this file exists.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.conf import settings
from django.urls import reverse

pytestmark = [pytest.mark.django_db, pytest.mark.integration]

# Anything that means "the template engine did not process this".
LEAK_PATTERNS = (
    re.compile(r"\{#"),
    re.compile(r"#\}"),
    re.compile(r"\{%\s*(comment|endcomment|if|for|trans|blocktrans)\b"),
    re.compile(r"\{\{\s*\w"),
)


def assert_no_template_leak(html: str, where: str) -> None:
    for pattern in LEAK_PATTERNS:
        match = pattern.search(html)
        assert match is None, (
            f"{where}: unrendered template syntax {match.group(0)!r} at offset {match.start()}"
        )


class TestTemplateSourceFiles:
    def test_no_multiline_hash_comments(self):
        """``{# ... #}`` is single-line only.

        Wrapped onto a second line it stops being a comment and Django prints
        it to the page. This shipped once already — the sidebar rendered its own
        explanatory note next to every nav item.
        """
        offenders: list[str] = []
        for path in sorted(Path(settings.BASE_DIR / "templates").rglob("*.html")):
            source = path.read_text(encoding="utf-8")
            for match in re.finditer(r"\{#.*?#\}", source, re.S):
                if "\n" in match.group(0):
                    line = source[: match.start()].count("\n") + 1
                    offenders.append(f"{path.name}:{line}")
        assert not offenders, (
            "Multi-line {# #} comments render as visible text; use "
            f"{{% comment %}} instead. Found at: {', '.join(offenders)}"
        )


class TestRenderedPages:
    def test_login_page_is_clean(self, client):
        response = client.get(reverse("accounts:login"))
        assert response.status_code == 200
        assert_no_template_leak(response.content.decode(), "login")

    def test_dashboard_is_clean(self, client, receptionist):
        client.force_login(receptionist)
        response = client.get(reverse("dashboard:home"))
        assert response.status_code == 200
        assert_no_template_leak(response.content.decode(), "dashboard")

    def test_profile_is_clean(self, client, receptionist):
        client.force_login(receptionist)
        response = client.get(reverse("accounts:profile"))
        assert response.status_code == 200
        assert_no_template_leak(response.content.decode(), "profile")

    def test_dashboard_shows_the_hotel_name(self, client, receptionist, hotel):
        client.force_login(receptionist)
        body = client.get(reverse("dashboard:home")).content.decode()
        assert hotel.name in body

    def test_unbuilt_modules_are_reachable_and_phase_tagged(self, client, db):
        """The roadmap should be visible in the product, not only in goal.txt.

        Earlier these items were rendered disabled, which reads as a broken app.
        They now link to their own page; the phase tag is what marks them as
        unfinished.
        """
        from django.contrib.auth import get_user_model

        admin = get_user_model().objects.create_superuser(
            email="owner@test.local", password="test-pass-12345", full_name="Owner"
        )
        client.force_login(admin)
        body = client.get(reverse("dashboard:home")).content.decode()
        assert "is-pending" in body
        assert "phase-tag" in body
        assert 'href="/housekeeping/"' in body

    def test_sidebar_hides_what_the_role_cannot_reach(self, client, db, receptionist):
        """A menu a user can never open should not be in their sidebar at all."""
        from django.contrib.auth import get_user_model

        admin = get_user_model().objects.create_superuser(
            email="owner2@test.local", password="test-pass-12345", full_name="Owner"
        )
        client.force_login(admin)
        admin_body = client.get(reverse("dashboard:home")).content.decode()

        client.force_login(receptionist)
        staff_body = client.get(reverse("dashboard:home")).content.decode()

        assert admin_body.count("nav-link-ashos") > staff_body.count("nav-link-ashos")
        assert "Dashboard" in staff_body  # the one item with no permission gate


class TestAIStatusBadge:
    """Three states. "Enabled but unconfigured" must never render as green."""

    def test_online_when_a_provider_is_configured(self, client, receptionist, settings):
        client.force_login(receptionist)
        body = client.get(reverse("dashboard:home")).content.decode()
        # config/settings/test.py pins the fake provider, which needs no key.
        assert "AI Concierge Online" in body

    def test_unconfigured_when_no_api_key(self, client, receptionist, settings):
        settings.AI = {
            **settings.AI,
            "LLM": {**settings.AI["LLM"], "provider": "openai_compatible", "api_key": ""},
        }
        from services.ai import registry

        registry.invalidate()

        client.force_login(receptionist)
        body = client.get(reverse("dashboard:home")).content.decode()
        assert "AI not configured" in body
        assert "AI Concierge Online" not in body

    def test_manual_mode_when_kill_switch_is_on(self, client, receptionist, settings):
        settings.AI = {**settings.AI, "KILL_SWITCH": True}
        client.force_login(receptionist)
        body = client.get(reverse("dashboard:home")).content.decode()
        assert "Manual mode" in body


class TestTheStaffConsoleCannotServeStaleAssets:
    """Every module page hangs off base.html, and base.html used {% static %}.

    The dev static handler sends Last-Modified and no Cache-Control, so the browser
    applies its own heuristic and reuses often enough that an edited stylesheet
    silently does not load. What that looks like is a page whose CSS appears
    broken — reported as exactly that — while the file on the server is correct and
    the half hour afterwards goes on debugging code that already works.

    The kiosk templates used {% asset %} from the start; the console did not.
    """

    def test_base_uses_the_versioned_tag_for_its_own_assets(self):
        from pathlib import Path

        source = (Path(__file__).parents[2] / "templates" / "base.html").read_text(encoding="utf-8")

        assert "{% asset 'css/ashos.css' %}" in source
        assert "{% asset 'js/ashos.js' %}" in source
        assert "{% static 'css/" not in source
        assert "{% static 'js/" not in source

    def test_a_rendered_page_ships_a_versioned_stylesheet_url(self, client, receptionist, settings):
        """The proof at the other end: the URL in the HTML carries a version, so an
        edit to the file changes the URL and the browser has to fetch it.

        DEBUG on, because that is the whole point of the tag: in production
        WhiteNoise's manifest storage already puts a content hash in the filename,
        and a query string there would only defeat the CDN. The suite runs with
        DEBUG off, so it has to be asked for here.
        """
        settings.DEBUG = True
        client.force_login(receptionist)
        body = client.get(reverse("dashboard:home")).content.decode()

        match = re.search(r'href="(/static/css/ashos\.css[^"]*)"', body)
        assert match, "no ashos.css link on the page"
        assert "?v=" in match.group(1), match.group(1)
