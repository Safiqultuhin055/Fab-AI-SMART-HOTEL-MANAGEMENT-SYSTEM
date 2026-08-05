from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from apps.core.utils import mask, money, short_code, uuid7


class TestUUID7:
    def test_is_version_7(self):
        assert uuid7().version == 7

    def test_is_time_ordered(self):
        """The whole reason we use v7 instead of v4: sortable inserts."""
        ids = [uuid7() for _ in range(50)]
        assert ids == sorted(ids), "uuid7 must be monotonically ordered within a run"

    def test_is_unique(self):
        assert len({uuid7() for _ in range(2000)}) == 2000

    def test_parses_as_uuid(self):
        assert isinstance(uuid.UUID(str(uuid7())), uuid.UUID)


class TestMoney:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (10.005, "10.01"),  # half-up, not banker's rounding
            (0.1 + 0.2, "0.30"),  # the classic float trap
            ("1234.567", "1234.57"),
            (0, "0.00"),
        ],
    )
    def test_quantises(self, value, expected):
        assert money(value) == Decimal(expected)

    def test_sum_of_lines_balances(self):
        """Folio lines must add up to the invoice total, exactly."""
        lines = [money(33.333) for _ in range(3)]
        assert sum(lines) == Decimal("99.99")


class TestShortCode:
    def test_length(self):
        assert len(short_code(8)) == 8

    def test_excludes_ambiguous_characters(self):
        """Guests read these over the phone; 0/O and 1/I must not appear."""
        sample = "".join(short_code(32) for _ in range(30))
        assert not set(sample) & set("01OIL")


class TestMask:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [("AB1234567", "*****4567"), ("123", "***"), ("", ""), (None, "")],
    )
    def test_masks(self, value, expected):
        assert mask(value) == expected
