"""Enable btree_gist.

Required by ``booking.ReservationRoom``'s exclusion constraint, which mixes
UUID equality with daterange overlap in a single GiST index. Without it the
constraint cannot be created at all:

    data type uuid has no default operator class for access method "gist"

Lives in ``core`` so it is guaranteed to run before any app that needs it, and
so a fresh database — a developer's, CI's, a new pilot's — gets it without
anyone remembering a manual step.
"""

from __future__ import annotations

from django.contrib.postgres.operations import BtreeGistExtension
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0002_module_access"),
    ]

    operations = [
        BtreeGistExtension(),
    ]
