"""Put real room photographs on the room types, so the kiosk shows a room.

Two sources, and the local one is the one a hotel will actually use:

    # the property's own photographs, named by room type code
    python manage.py import_room_photos --hotel GLH-001 --dir C:/photos --replace
    #   DLX-1.jpg  DLX-2.jpg  SEA-1.jpg  ...  -> Deluxe King, Sea View Suite

    # a demo set fetched from Unsplash, for a machine nobody has photographed
    python manage.py import_room_photos --hotel GLH-001 --demo-set --replace

``seed_pms`` draws a placeholder tile per type so the gallery is never empty
offline. This command is how those get replaced with photographs of rooms.

On the demo set: these are real interiors under the Unsplash licence (free use,
no attribution required), picked so a stakeholder demo looks like a hotel instead
of five gradients. They are still not *this* hotel's rooms, and a property that
ships them to guests unchanged is showing rooms it does not have — so the command
says so, loudly, to the operator running it. ``--dir`` is the answer for a real
property; the admin (Room types → a type → photos) is the answer for one
photograph. ``--label-demo`` puts the warning in the guest-facing caption too.

Idempotent: a type that already has a photo is skipped unless ``--replace``.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.rooms.models import RoomType, RoomTypePhoto
from apps.tenants.models import Hotel

#: Unsplash photo ids per room type code, each with the caption a guest reads.
#: Every one of these was looked at before it went in the list — an id that has
#: been reassigned, or a "hotel room" that turns out to be a swimming pool, is
#: worse than the drawn placeholder it replaces.
DEMO_SET: dict[str, list[tuple[str, str]]] = {
    "STD": [
        ("1631049307264-da0ec9d70304", "The room"),
        ("1616594039964-ae9021a400a0", "City view"),
    ],
    "DLX": [
        ("1566665797739-1674de7a421a", "The room"),
        ("1618773928121-c32242e63f39", "King bed"),
    ],
    "SEA": [
        ("1590490360182-c33d57733427", "The suite"),
        ("1582719508461-905c673771fd", "Sea view"),
    ],
    "TWN": [
        ("1595576508898-0ad5c879a061", "Twin beds"),
    ],
    "FAM": [
        ("1522708323590-d24dbb6b0267", "Living area"),
        ("1560448204-e02f11c3d0e2", "Family lounge"),
        ("1578683010236-d716f9a3f461", "The bedroom"),
    ],
}

#: Used for any room type code the set above does not know about, so a property
#: with its own codes still gets a room rather than nothing.
DEMO_FALLBACK: list[tuple[str, str]] = [
    ("1611892440504-42a792e24d32", "The room"),
    ("1584132967334-10e028bd69f7", "The grounds"),
]

#: Appended to every demo caption when --label-demo is passed. Off by default,
#: because the caption is read by a guest standing at the kiosk and "not this
#: hotel" in the middle of a booking is the property telling on itself. The
#: warning the command prints is aimed at the operator instead, who is the person
#: who can actually do something about it.
DEMO_CAPTION_SUFFIX = " · demo photo"

#: Wide enough for a full-screen lobby terminal, small enough that five of them
#: are not a 20 MB media directory.
DEMO_WIDTH = 1600

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


class Command(BaseCommand):
    help = "Attach room photographs to room types, from a folder or the demo set."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--hotel", default="GLH-001")
        parser.add_argument(
            "--dir",
            dest="directory",
            help="Folder of images named <ROOM_TYPE_CODE>-<n>.<ext>, e.g. DLX-1.jpg.",
        )
        parser.add_argument(
            "--demo-set",
            action="store_true",
            help="Fetch the curated demo photographs. Needs network access.",
        )
        parser.add_argument(
            "--replace",
            action="store_true",
            help="Delete the photos a type already has first — how the drawn placeholders go.",
        )
        parser.add_argument(
            "--label-demo",
            action="store_true",
            help="Mark every demo caption as a demo photo, where a guest can read it.",
        )

    @transaction.atomic
    def handle(self, *args: Any, **options: Any) -> None:
        if not options["directory"] and not options["demo_set"]:
            raise CommandError("Give --dir <folder> or --demo-set.")

        hotel = Hotel.all_objects.filter(code=options["hotel"].upper()).first()
        if hotel is None:
            raise CommandError(f"Unknown hotel code: {options['hotel']}")

        types = {
            room_type.code.upper(): room_type
            for room_type in RoomType.all_objects.filter(tenant=hotel, is_deleted=False)
        }
        if not types:
            raise CommandError(
                f"{hotel.code} has no room types yet. Run: manage.py seed_pms --hotel {hotel.code}"
            )

        files = (
            self._from_directory(Path(options["directory"]), types)
            if options["directory"]
            else self._from_demo_set(types, label=options["label_demo"])
        )

        written = 0
        for code, images in files.items():
            room_type = types[code]
            existing = RoomTypePhoto.all_objects.filter(room_type=room_type, is_deleted=False)
            if existing.exists():
                if not options["replace"]:
                    self.stdout.write(f"  skip      {code} already has photos (--replace to swap)")
                    continue
                # The file goes with the row. Django does not delete the object
                # behind an ImageField, so replacing a set of photos five times
                # leaves five sets in media/ — and the rows that pointed at them
                # are gone, so nothing will ever clean them up.
                for stale in existing:
                    if stale.image:
                        stale.image.delete(save=False)
                # Hard delete: a soft-deleted photo row is invisible to the kiosk
                # but would still count as "this type has photos" to a human
                # reading the admin.
                existing.hard_delete()

            for order, (name, caption, blob) in enumerate(images):
                RoomTypePhoto.all_objects.create(
                    tenant=hotel,
                    room_type=room_type,
                    image=ContentFile(blob, name=name),
                    caption=caption,
                    sort_order=order,
                )
                written += 1
            plural = "photo" if len(images) == 1 else "photos"
            self.stdout.write(self.style.SUCCESS(f"  {code:<9} {len(images)} {plural}"))

        self.stdout.write(f"\n{written} photographs stored for {hotel.name} ({hotel.code}).")
        if written:
            self.stdout.write("Kiosk shows them on the next booking turn — no restart needed.")
        if written and options["demo_set"]:
            # Said plainly, and to the operator rather than to the guest: these
            # are photographs of somebody else's rooms. Fine on a demo terminal,
            # a promise the property cannot keep on a real one.
            self.stdout.write(
                self.style.WARNING(
                    "\nThese are stock interiors, NOT this property's rooms. Replace them\n"
                    "with the hotel's own photographs before a guest sees this kiosk:\n"
                    f"  manage.py import_room_photos --hotel {hotel.code} "
                    "--dir <folder> --replace\n"
                    "  or admin -> Room types -> (a type) -> photos"
                )
            )

    # ------------------------------------------------------------------ sources

    def _from_directory(
        self, directory: Path, types: dict[str, RoomType]
    ) -> dict[str, list[tuple[str, str, bytes]]]:
        """Local files, matched to a room type by the part before the first dash.

        ``DLX-1.jpg`` and ``dlx_2.png`` both land on DLX. A file that matches no
        room type is named in the output rather than dropped quietly — a typo in
        a filename is the most likely reason a photo "did not import".
        """
        if not directory.is_dir():
            raise CommandError(f"Not a folder: {directory}")

        found: dict[str, list[tuple[str, str, bytes]]] = {}
        unmatched: list[str] = []
        for path in sorted(directory.iterdir()):
            if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
                continue
            code = path.stem.replace("_", "-").split("-")[0].upper()
            if code not in types:
                unmatched.append(path.name)
                continue
            found.setdefault(code, []).append((path.name, "", path.read_bytes()))

        if unmatched:
            self.stdout.write(
                self.style.WARNING(
                    "  ignored   " + ", ".join(unmatched) + " (no room type with that code)"
                )
            )
        if not found:
            raise CommandError(
                f"No usable images in {directory}. Name them by room type code, e.g. "
                + ", ".join(f"{code}-1.jpg" for code in sorted(types)[:3])
            )
        return found

    def _from_demo_set(
        self, types: dict[str, RoomType], *, label: bool = False
    ) -> dict[str, list[tuple[str, str, bytes]]]:
        found: dict[str, list[tuple[str, str, bytes]]] = {}
        suffix = DEMO_CAPTION_SUFFIX if label else ""
        for code in types:
            plan = DEMO_SET.get(code, DEMO_FALLBACK)
            images: list[tuple[str, str, bytes]] = []
            for index, (photo_id, caption) in enumerate(plan, start=1):
                url = f"https://images.unsplash.com/photo-{photo_id}?w={DEMO_WIDTH}&q=80&fm=jpg"
                try:
                    blob = self._fetch(url)
                except OSError as exc:
                    # One dead id must not cost the other four types their photos.
                    self.stderr.write(self.style.WARNING(f"  failed    {code} {photo_id}: {exc}"))
                    continue
                images.append((f"{code.lower()}-{index}.jpg", f"{caption}{suffix}", blob))
            if images:
                found[code] = images

        if not found:
            raise CommandError(
                "Could not fetch any demo photographs — no network, most likely. "
                "Use --dir <folder> with your own images instead."
            )
        return found

    def _fetch(self, url: str) -> bytes:
        # noqa on both lines: the URL is built here from a fixed https host and a
        # hard-coded photo id, never from anything a user typed.
        request = urllib.request.Request(  # noqa: S310 - fixed host, no user input
            url, headers={"User-Agent": "ASHOS/0.1 (+seed)"}
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - fixed host
                blob = response.read()
        except urllib.error.HTTPError as exc:
            raise OSError(f"HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise OSError(str(exc.reason)) from exc
        # A CDN error page is a 200 with HTML in it. Storing that as a .jpg gives
        # a broken image on a lobby screen, which is the hardest kind of bug to
        # read backwards.
        if not blob.startswith(b"\xff\xd8") and not blob.startswith(b"\x89PNG"):
            raise OSError("not an image")
        return blob
