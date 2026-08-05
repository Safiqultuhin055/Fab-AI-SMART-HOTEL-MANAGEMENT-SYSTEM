"""Fill the PMS with a believable hotel: rooms, rates, guests, stays, folios.

Kept separate from ``seed_demo`` because it is a different kind of data. That
command sets up tenancy and AI configuration; this one produces an operating
hotel — arrivals today, guests in house, departures, folios with real charges
and a night audit that balances.

    python manage.py seed_pms --hotel GLH-001
    python manage.py seed_pms --hotel GLH-001 --flush --seed 7

Deterministic and idempotent. Everything it creates is tagged (guest emails at
``@demo.ashos.local``, reservations marked in ``internal_notes``) so ``--flush``
removes exactly its own rows and nothing an operator typed by hand.
"""

from __future__ import annotations

import random
from datetime import timedelta
from decimal import Decimal
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from apps.billing.models import ChargeType, PaymentMethod
from apps.booking.models import BookingSource, Reservation, ReservationStatus
from apps.core.demo_data import (
    AMENITIES,
    BANGLA_NAMES,
    DEMO_GUEST_DOMAIN,
    DEMO_MARKER,
    EXTRA_CHARGES,
    INTERNATIONAL_NAMES,
    RATE_PLANS,
    ROOM_TYPES,
    SPECIAL_REQUESTS,
)
from apps.core.exceptions import ASHOSError
from apps.core.utils import money
from apps.guests.models import Guest, GuestTier
from apps.rooms.models import Amenity, RatePlan, Room, RoomStatus, RoomType
from apps.tenants.models import Hotel
from services.billing import folio as billing
from services.booking import reservations as booking


class Command(BaseCommand):
    help = "Seed rooms, rates, guests, reservations and folios for a hotel."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--hotel", default="GLH-001")
        parser.add_argument("--seed", type=int, default=42)
        parser.add_argument("--guests", type=int, default=40)
        parser.add_argument("--history-days", type=int, default=14)
        parser.add_argument("--future-days", type=int, default=10)
        parser.add_argument(
            "--occupancy",
            type=float,
            default=0.62,
            help="Target occupancy the arrival volume is sized for.",
        )
        parser.add_argument("--flush", action="store_true")
        parser.add_argument(
            "--skip-photos",
            action="store_true",
            help="Do not generate the placeholder room photos the kiosk gallery shows.",
        )

    @transaction.atomic
    def handle(self, *args: Any, **options: Any) -> None:
        rng = random.Random(options["seed"])  # noqa: S311 - reproducible fixtures
        self.target_occupancy = max(0.05, min(0.95, options["occupancy"]))

        hotel = Hotel.all_objects.filter(code=options["hotel"].upper()).first()
        if hotel is None:
            raise CommandError(f"Unknown hotel code: {options['hotel']}")

        if options["flush"]:
            self._flush(hotel)

        amenities = self._amenities(hotel)
        types = self._room_types(hotel, amenities, rng)
        if not options["skip_photos"]:
            self._room_photos(types)
        rooms = self._rooms(hotel, types, rng)
        plans = self._rate_plans(hotel)
        guests = self._guests(hotel, rng, options["guests"])
        self._reservations(
            hotel, types, plans, guests, rng, options["history_days"], options["future_days"]
        )
        self._report(hotel, len(rooms))

    # ------------------------------------------------------------------ flush

    def _flush(self, hotel: Hotel) -> None:
        """Remove only seeded rows, children first."""
        from apps.billing.models import Folio, FolioLine, Invoice, Payment

        seeded = Reservation.all_objects.filter(
            tenant=hotel, internal_notes__startswith=DEMO_MARKER
        )
        folios = Folio.all_objects.filter(reservation__in=seeded)

        counts = {
            "payments": Payment.all_objects.filter(folio__in=folios).hard_delete()[0],
            "invoices": Invoice.all_objects.filter(folio__in=folios).hard_delete()[0],
            "lines": FolioLine.all_objects.filter(folio__in=folios).hard_delete()[0],
            "folios": folios.hard_delete()[0],
            "reservations": seeded.hard_delete()[0],
            "guests": Guest.all_objects.filter(
                tenant=hotel, email__endswith=DEMO_GUEST_DOMAIN
            ).hard_delete()[0],
        }
        self.stdout.write(
            self.style.WARNING(
                "  flushed   " + " · ".join(f"{value} {label}" for label, value in counts.items())
            )
        )

    # ------------------------------------------------------------------ steps

    def _amenities(self, hotel: Hotel) -> list[Amenity]:
        created = []
        for name in AMENITIES:
            amenity, _ = Amenity.all_objects.get_or_create(tenant=hotel, name=name)
            created.append(amenity)
        return created

    def _room_types(self, hotel: Hotel, amenities: list[Amenity], rng) -> list[RoomType]:
        types: list[RoomType] = []
        for order, spec in enumerate(ROOM_TYPES):
            room_type, created = RoomType.all_objects.get_or_create(
                tenant=hotel,
                code=str(spec["code"]),
                defaults={
                    "name": spec["name"],
                    "base_occupancy": spec["base_occupancy"],
                    "max_occupancy": spec["max_occupancy"],
                    "bed_type": spec["bed_type"],
                    "base_rate": Decimal(str(spec["base_rate"])),
                    "extra_person_rate": Decimal(str(spec["extra_person_rate"])),
                    "size_sqm": spec["size_sqm"],
                    "view": spec["view"],
                    "sort_order": order,
                    "description": f"{spec['name']} with a {spec['view']} view.",
                },
            )
            if created:
                room_type.amenities.set(rng.sample(amenities, k=rng.randint(4, 8)))
            types.append(room_type)
        self.stdout.write(f"  types     {len(types)} room types")
        return types

    def _room_photos(self, types: list[RoomType]) -> None:
        """Placeholder tiles so the kiosk's room gallery has something to show.

        Drawn rather than downloaded, so the seed works with no network and never
        claims somebody else's room is this hotel's. They look like placeholders
        because they are placeholders.

        For a demo that has to look like a hotel, and for a real property's own
        photographs:

            manage.py import_room_photos --hotel GLH-001 --demo-set --replace
            manage.py import_room_photos --hotel GLH-001 --dir <folder> --replace

        Skipped for any type that already has a photo, so running the seed twice
        never buries an operator's uploads under generated ones.
        """
        from io import BytesIO

        from django.core.files.base import ContentFile
        from PIL import Image, ImageDraw, ImageFont

        from apps.rooms.models import RoomTypePhoto

        # One hue per type, walked round the wheel so four cards side by side in
        # the gallery are visibly different rooms rather than four blue squares.
        hues = [(34, 60, 110), (18, 74, 96), (52, 46, 112), (16, 82, 74), (74, 52, 88)]
        made = 0
        for index, room_type in enumerate(types):
            if RoomTypePhoto.all_objects.filter(room_type=room_type, is_deleted=False).exists():
                continue

            top = hues[index % len(hues)]
            width, height = 1024, 768
            image = Image.new("RGB", (width, height), top)
            draw = ImageDraw.Draw(image)
            for row in range(height):
                fade = row / height
                draw.line(
                    [(0, row), (width, row)],
                    fill=tuple(int(channel * (1 - 0.55 * fade)) for channel in top),
                )

            title = ImageFont.load_default(size=64)
            small = ImageFont.load_default(size=30)
            draw.text((56, 96), room_type.name, font=title, fill=(240, 244, 255))
            draw.text(
                (58, 190),
                f"{room_type.code} · {room_type.view or 'no view set'} · "
                f"sleeps {room_type.max_occupancy}",
                font=small,
                fill=(190, 200, 225),
            )
            draw.text(
                (58, height - 90), "DEMO IMAGE — NOT A REAL ROOM", font=small, fill=(255, 210, 120)
            )

            buffer = BytesIO()
            image.save(buffer, format="JPEG", quality=82)
            RoomTypePhoto.all_objects.create(
                tenant=room_type.tenant,
                room_type=room_type,
                image=ContentFile(buffer.getvalue(), name=f"demo-{room_type.code.lower()}.jpg"),
                caption="Demo image — replace with a real photograph",
            )
            made += 1

        self.stdout.write(f"  photos    {made} placeholder room photos")

    def _rooms(self, hotel: Hotel, types: list[RoomType], rng) -> list[Room]:
        """Lay out floors realistically: cheaper rooms low, suites high."""
        target = hotel.total_rooms or 40
        plan: list[RoomType] = []
        for spec, room_type in zip(ROOM_TYPES, types, strict=True):
            plan.extend([room_type] * max(1, round(target * float(spec["share"]))))
        plan = plan[:target]

        rooms: list[Room] = []
        per_floor = 10
        for index, room_type in enumerate(plan):
            floor = index // per_floor + 1
            number = f"{floor}{index % per_floor + 1:02d}"
            room, created = Room.all_objects.get_or_create(
                tenant=hotel,
                number=number,
                defaults={
                    "room_type": room_type,
                    "floor": floor,
                    # A real board is never all-clean: a few dirty, the odd
                    # room out of order. Dashboards that only ever show green
                    # hide the states staff actually work with.
                    "status": rng.choices(
                        [
                            RoomStatus.VACANT_CLEAN,
                            RoomStatus.VACANT_DIRTY,
                            RoomStatus.OUT_OF_ORDER,
                        ],
                        weights=[80, 15, 5],
                    )[0],
                },
            )
            if created:
                rooms.append(room)
        total = Room.all_objects.filter(tenant=hotel, is_deleted=False).count()
        self.stdout.write(f"  rooms     {len(rooms)} created, {total} total")
        return rooms

    def _rate_plans(self, hotel: Hotel) -> list[RatePlan]:
        plans = []
        for spec in RATE_PLANS:
            plan, _ = RatePlan.all_objects.get_or_create(
                tenant=hotel,
                code=str(spec["code"]),
                defaults={
                    "name": spec["name"],
                    "discount_percent": Decimal(str(spec["discount_percent"])),
                    "includes_breakfast": spec["includes_breakfast"],
                    "is_refundable": spec["is_refundable"],
                    "is_default": spec["is_default"],
                },
            )
            plans.append(plan)
        self.stdout.write(f"  rates     {len(plans)} rate plans")
        return plans

    def _guests(self, hotel: Hotel, rng, count: int) -> list[Guest]:
        names = list(BANGLA_NAMES) + list(INTERNATIONAL_NAMES)
        rng.shuffle(names)

        guests: list[Guest] = []
        for index in range(count):
            full = names[index % len(names)]
            first, _, last = full.partition(" ")
            suffix = index // len(names)
            email = f"{slugify(full)}{suffix or ''}@{DEMO_GUEST_DOMAIN}"

            guest, _ = Guest.all_objects.get_or_create(
                tenant=hotel,
                email=email,
                defaults={
                    "first_name": first,
                    "last_name": last or "Guest",
                    "phone": f"+8801{rng.randint(300000000, 999999999)}",
                    "nationality": rng.choices(
                        ["BD", "IN", "GB", "US", "CN", "AE"], weights=[70, 8, 6, 6, 5, 5]
                    )[0],
                    "language": rng.choices(["bn", "en"], weights=[60, 40])[0],
                    "tier": rng.choices(
                        [GuestTier.STANDARD, GuestTier.SILVER, GuestTier.GOLD, GuestTier.VIP],
                        weights=[70, 18, 9, 3],
                    )[0],
                    "preferences": rng.choice(
                        [
                            {},
                            {"floor": "high"},
                            {"bed": "king"},
                            {"view": "sea"},
                            {"floor": "high", "view": "sea"},
                        ]
                    ),
                    "city": rng.choice(["Dhaka", "Chittagong", "Sylhet", "Khulna", "London"]),
                    "country": "BD",
                },
            )
            guests.append(guest)
        self.stdout.write(f"  guests    {len(guests)}")
        return guests

    def _reservations(self, hotel, types, plans, guests, rng, history_days, future_days) -> None:
        """Past stays, tonight's house, and a forward book.

        Uses the real service layer rather than raw inserts, so the seeded data
        obeys every rule the product enforces: availability, the exclusion
        constraint, folio posting and check-in transitions. If seeding can
        produce a state, so can reception.
        """
        today = timezone.localdate()
        created = checked_out = in_house = future = 0
        conflicts = 0

        # Arrivals per day are derived from the room count and a target
        # occupancy, not picked out of the air. A 120-room hotel with three
        # arrivals a day reads as a failing business, and every occupancy,
        # ADR and RevPAR figure on the dashboard would be nonsense.
        sellable = max(1, Room.all_objects.filter(tenant=hotel, is_deleted=False).count())
        average_nights = 2.6
        base_arrivals = max(1, round(sellable * self.target_occupancy / average_nights))

        window = range(-history_days, future_days)
        for offset in window:
            arrival = today + timedelta(days=offset)
            # A forward book tapers: the further out, the fewer confirmed.
            taper = 1.0 if offset <= 0 else max(0.25, 1 - offset / (future_days + 4))
            volume = max(0, round(rng.gauss(base_arrivals * taper, base_arrivals * 0.2)))

            for _ in range(volume):
                nights = rng.choices([1, 2, 3, 4, 5, 7], weights=[25, 30, 20, 12, 8, 5])[0]
                departure = arrival + timedelta(days=nights)
                room_type = rng.choices(types, weights=[40, 28, 17, 10, 5])[0]
                guest = rng.choice(guests)
                adults = min(
                    room_type.max_occupancy, rng.choices([1, 2, 3], weights=[25, 60, 15])[0]
                )

                marker = f"{DEMO_MARKER}:{arrival.isoformat()}:{guest.pk}:{nights}"
                if Reservation.all_objects.filter(tenant=hotel, internal_notes=marker).exists():
                    continue

                try:
                    reservation = self._make(
                        hotel, guest, room_type, plans, arrival, departure, adults, rng, marker
                    )
                except ASHOSError:
                    # Sold out for those dates. Realistic, and exactly what the
                    # availability rules are supposed to do.
                    conflicts += 1
                    continue

                created += 1
                if departure <= today:
                    self._complete_stay(reservation, rng)
                    checked_out += 1
                elif arrival <= today < departure:
                    self._occupy(reservation, rng, today)
                    in_house += 1
                else:
                    future += 1

        self.stdout.write(
            f"  bookings  {created} created "
            f"({checked_out} completed · {in_house} in house · {future} upcoming), "
            f"{conflicts} refused as sold out"
        )

    def _make(self, hotel, guest, room_type, plans, arrival, departure, adults, rng, marker):
        return booking.create(
            hotel=hotel,
            guest=guest,
            check_in=arrival,
            check_out=departure,
            room_type=room_type,
            adults=adults,
            rate_plan=rng.choice(plans),
            source=rng.choices(
                [
                    BookingSource.WALK_IN,
                    BookingSource.WEBSITE,
                    BookingSource.PHONE,
                    BookingSource.KIOSK,
                    BookingSource.OTA,
                ],
                weights=[25, 30, 15, 15, 15],
            )[0],
            special_requests=rng.choice(SPECIAL_REQUESTS),
            internal_notes=marker,
            # Backfilling history. Reception can never pass this.
            allow_past=True,
        )

    def _occupy(self, reservation, rng, today) -> None:
        booking.check_in(reservation, user=None)
        folio = billing.open_folio(reservation)
        nights_so_far = max(1, (today - reservation.check_in).days)
        self._post_room_nights(reservation, folio, nights_so_far)
        self._post_extras(folio, rng, reservation.check_in)

    def _complete_stay(self, reservation, rng) -> None:
        booking.check_in(reservation, user=None)
        folio = billing.open_folio(reservation)
        self._post_room_nights(reservation, folio, reservation.nights)
        self._post_extras(folio, rng, reservation.check_in)

        folio.recalculate()
        if folio.balance > 0:
            billing.post_payment(
                folio,
                method=rng.choices(
                    [PaymentMethod.CASH, PaymentMethod.CARD, PaymentMethod.BKASH],
                    weights=[45, 35, 20],
                )[0],
                amount=folio.balance,
                reference=f"demo-{reservation.code}",
            )
        booking.check_out(reservation, user=None)

    def _post_room_nights(self, reservation, folio, nights: int) -> None:
        from services.rooms import pricing

        hotel = reservation.tenant
        for index in range(nights):
            day = reservation.check_in + timedelta(days=index)
            if day >= reservation.check_out:
                break
            for allocation in reservation.allocations.all():
                rate, source = pricing.nightly_rate(
                    allocation.room_type, reservation.rate_plan, day
                )
                label = allocation.room.number if allocation.room else allocation.room_type.name
                billing.post_charge(
                    folio,
                    charge_type=ChargeType.ROOM,
                    description=f"Room {label} — {day:%d %b} ({source})",
                    amount=rate,
                    on_date=day,
                    source_module="seed_pms",
                )
                service = money(rate * Decimal(hotel.service_charge_rate) / Decimal("100"))
                if service:
                    billing.post_charge(
                        folio,
                        charge_type=ChargeType.SERVICE,
                        description=f"Service charge {hotel.service_charge_rate}%",
                        amount=service,
                        on_date=day,
                        source_module="seed_pms",
                    )
                tax = money((rate + service) * Decimal(hotel.tax_rate) / Decimal("100"))
                if tax:
                    billing.post_charge(
                        folio,
                        charge_type=ChargeType.TAX,
                        description=f"VAT {hotel.tax_rate}%",
                        amount=tax,
                        on_date=day,
                        source_module="seed_pms",
                    )

    def _post_extras(self, folio, rng, start) -> None:
        for _ in range(rng.choices([0, 1, 2, 3], weights=[35, 35, 20, 10])[0]):
            kind, description, low, high = rng.choice(EXTRA_CHARGES)
            amount = money(rng.uniform(float(low), float(high)))
            billing.post_charge(
                folio,
                charge_type=kind,
                description=description,
                amount=amount,
                on_date=start,
                source_module="seed_pms",
            )

    # ----------------------------------------------------------------- report

    def _report(self, hotel: Hotel, new_rooms: int) -> None:
        from django.db.models import Count, Q, Sum

        from apps.billing.models import Folio
        from services.booking import availability

        today = timezone.localdate()
        stats = Reservation.all_objects.filter(tenant=hotel, is_deleted=False).aggregate(
            total=Count("id"),
            in_house=Count("id", filter=Q(status=ReservationStatus.CHECKED_IN)),
            revenue=Sum("grand_total"),
        )
        folios = Folio.all_objects.filter(tenant=hotel, is_deleted=False).aggregate(
            charges=Sum("charges_total"), outstanding=Sum("balance")
        )
        occ = availability.occupancy(hotel, today)

        self.stdout.write(self.style.SUCCESS(f"\nPMS seeded for {hotel}."))
        self.stdout.write(f"  rooms          {occ['total_rooms']} sellable ({new_rooms} new)")
        self.stdout.write(
            f"  occupancy      {occ['occupied']}/{occ['total_rooms']} "
            f"({occ['occupancy_rate']:.0f}%) tonight"
        )
        self.stdout.write(f"  reservations   {stats['total']} ({stats['in_house']} in house)")
        self.stdout.write(f"  folio charges  {folios['charges'] or 0} {hotel.currency}")
        self.stdout.write(f"  outstanding    {folios['outstanding'] or 0} {hotel.currency}")
        self.stdout.write(f"  guests         {Guest.all_objects.filter(tenant=hotel).count()}")
