"""Pagination policy (goal.txt D18).

Cursor pagination, not offset. Reservation and folio lists are written to
constantly; with ``?page=2`` a row inserted between requests silently shifts
every subsequent page and the user never sees one record. Cursor paging is also
O(1) on deep pages, which matters for audit and usage logs that grow forever.
"""

from __future__ import annotations

from rest_framework.pagination import CursorPagination as DRFCursorPagination
from rest_framework.pagination import PageNumberPagination


class CursorPagination(DRFCursorPagination):
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 200
    ordering = "-created_at"


class SmallCursorPagination(CursorPagination):
    page_size = 10


class LegacyPageNumberPagination(PageNumberPagination):
    """Only for endpoints a human paginates by hand, e.g. admin-style reports."""

    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 200
