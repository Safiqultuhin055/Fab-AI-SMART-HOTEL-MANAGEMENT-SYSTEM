"""Enable the pgvector extension.

Runs first and stands alone: every vector column added later (kb_chunk,
guest_face, hotel_image) depends on ``apps.core`` so the extension is
guaranteed to exist before the first ``VECTOR(n)`` column is created.

Requires a superuser or a role granted CREATE on the database. In the Docker
stack the image ships the extension and the migration only registers it.
"""

from __future__ import annotations

from django.db import migrations
from pgvector.django import VectorExtension


class Migration(migrations.Migration):
    initial = True

    dependencies: list = []

    operations = [
        VectorExtension(),
    ]
