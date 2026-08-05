"""Dashboard has no tables of its own.

KPIs are computed from the operational modules (rooms, booking, billing) in
``services/analytics``. A denormalised snapshot table arrives in P4, when the
query cost is measured rather than guessed.
"""

from __future__ import annotations
