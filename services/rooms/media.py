"""Pictures of a room type, in the shape a guest-facing screen wants them.

The kiosk shows the guest what they are about to book while they are booking it —
"DLX · 18216 BDT" tells somebody standing in a lobby almost nothing, and a
photograph of the room tells them the thing they actually want to know.

One query for every room type on the screen, never one per card: the booking
turn already runs a model call, and N+1 image lookups behind it are how a
conversation that felt instant starts feeling slow.

A room type with no photograph uploaded returns an empty ``photos`` list rather
than a stand-in image. A generic stock bedroom shown as *this* hotel's room is a
picture of a room the guest will not be given.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from apps.rooms.models import RoomType


def _url(image) -> str:
    """The image's URL, or "" if the file is gone.

    Storage backends raise on ``.url`` when the underlying file has been removed
    — a media directory wiped between deploys, an S3 object deleted by hand. One
    missing file must not take the kiosk's whole booking panel down with it.
    """
    try:
        return image.url
    except Exception:  # noqa: BLE001 - any storage error means "no picture"
        return ""


def gallery(room_type_ids: Iterable[Any], *, limit: int = 6) -> dict[str, list[dict[str, str]]]:
    """Photos for these room types, keyed by room type id as a string.

    ``limit`` caps how many are returned per type. A hotelier who uploads thirty
    pictures of the same suite should not push thirty of them down to a lobby
    terminal on every turn of the conversation.
    """
    from apps.rooms.models import RoomTypePhoto

    ids = [str(pk) for pk in room_type_ids if pk]
    if not ids:
        return {}

    out: dict[str, list[dict[str, str]]] = {pk: [] for pk in ids}
    rows = RoomTypePhoto.all_objects.filter(is_deleted=False, room_type_id__in=ids).only(
        "room_type_id", "image", "caption"
    )
    for photo in rows:
        bucket = out.setdefault(str(photo.room_type_id), [])
        if len(bucket) >= limit:
            continue
        url = _url(photo.image)
        if url:
            bucket.append({"url": url, "caption": photo.caption})
    return out


def describe(room_type: RoomType, photos: list[dict[str, str]] | None = None) -> dict[str, Any]:
    """The facts a guest reads next to the picture.

    Deliberately not the price: the kiosk's booking card already carries the
    re-quoted total, and a second number rendered from a different source is how
    a screen ends up showing two prices for one stay.

    ``bed`` is the raw choice value, not ``get_bed_type_display()``. That label is
    resolved against the *request's* locale, and the kiosk's language belongs to
    the conversation — so "King" appeared on a Bangla screen. The words live in
    ``apps.reception.copy`` with the rest of what a guest reads, and the client
    looks them up.
    """
    return {
        "code": room_type.code,
        "name": room_type.name,
        "view": room_type.view or "",
        "bed": room_type.bed_type,
        "sleeps": room_type.max_occupancy,
        "size_sqm": room_type.size_sqm,
        "photos": photos or [],
    }
