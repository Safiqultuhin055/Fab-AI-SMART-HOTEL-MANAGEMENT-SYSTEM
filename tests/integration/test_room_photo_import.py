"""Getting real photographs onto the room types.

The kiosk gallery is only as useful as what a hotelier managed to load into it,
so the import has to be forgiving about how files are named and loud about the
ones it could not place. A photo that silently did not import is a room the guest
never sees a picture of, and nobody finds out until a demo.

The demo set is not exercised here: it reaches Unsplash, and a unit test that
touches the network is a bug rather than a flake (goal.txt D14). What is tested
is everything around it — matching, ordering, replacing and refusing.
"""

from __future__ import annotations

import io
from decimal import Decimal

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.rooms.models import RoomType, RoomTypePhoto

pytestmark = [pytest.mark.django_db, pytest.mark.integration]

# A one-pixel PNG. The import never decodes an image; this is not a Pillow test.
PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c6300010000050001"
    "0d0a2db40000000049454e44ae426082"
)


@pytest.fixture
def types(hotel):
    from apps.core.context import set_request_context

    set_request_context(tenant_id=str(hotel.pk))
    for code, name in (("DLX", "Deluxe King"), ("STD", "Standard Single")):
        RoomType.all_objects.create(
            tenant=hotel, code=code, name=name, base_rate=Decimal("5000.00")
        )
    return RoomType.all_objects.filter(tenant=hotel)


def run(hotel, **kwargs) -> str:
    out = io.StringIO()
    call_command("import_room_photos", hotel=hotel.code, stdout=out, stderr=out, **kwargs)
    return out.getvalue()


def write(directory, name: str, blob: bytes = PNG_1PX):
    path = directory / name
    path.write_bytes(blob)
    return path


class TestFromAFolder:
    def test_files_land_on_the_room_type_named_in_the_filename(self, hotel, types, tmp_path):
        write(tmp_path, "DLX-1.png")
        write(tmp_path, "DLX-2.png")
        write(tmp_path, "STD-1.png")
        run(hotel, directory=str(tmp_path))

        deluxe = RoomTypePhoto.all_objects.filter(room_type__code="DLX", is_deleted=False)
        assert deluxe.count() == 2
        assert RoomTypePhoto.all_objects.filter(room_type__code="STD").count() == 1
        # Order is the order they will be shown in, so it cannot be arbitrary.
        assert list(deluxe.values_list("sort_order", flat=True)) == [0, 1]

    def test_the_separator_and_the_case_do_not_have_to_be_perfect(self, hotel, types, tmp_path):
        """A hotelier renaming forty files by hand will not be consistent, and
        being strict about it means photos that quietly did not import."""
        write(tmp_path, "dlx_1.png")
        write(tmp_path, "Dlx-2.PNG")
        run(hotel, directory=str(tmp_path))

        assert RoomTypePhoto.all_objects.filter(room_type__code="DLX").count() == 2

    def test_a_file_matching_no_room_type_is_named_rather_than_dropped(
        self, hotel, types, tmp_path
    ):
        write(tmp_path, "DLX-1.png")
        write(tmp_path, "penthouse-1.png")
        output = run(hotel, directory=str(tmp_path))

        assert "penthouse-1.png" in output
        assert RoomTypePhoto.all_objects.count() == 1

    def test_things_that_are_not_images_are_left_alone(self, hotel, types, tmp_path):
        write(tmp_path, "DLX-1.png")
        write(tmp_path, "notes.txt", b"not a photo")
        run(hotel, directory=str(tmp_path))

        assert RoomTypePhoto.all_objects.count() == 1

    def test_a_folder_with_nothing_usable_is_an_error_not_a_silent_success(
        self, hotel, types, tmp_path
    ):
        write(tmp_path, "readme.txt", b"hello")
        with pytest.raises(CommandError, match="No usable images"):
            run(hotel, directory=str(tmp_path))


class TestReplacing:
    def test_existing_photos_are_kept_unless_asked(self, hotel, types, tmp_path):
        write(tmp_path, "DLX-1.png")
        run(hotel, directory=str(tmp_path))
        first = set(RoomTypePhoto.all_objects.values_list("pk", flat=True))

        output = run(hotel, directory=str(tmp_path))

        assert "skip" in output
        assert set(RoomTypePhoto.all_objects.values_list("pk", flat=True)) == first

    def test_replace_swaps_them_out_completely(self, hotel, types, tmp_path):
        """This is how the drawn placeholders from seed_pms are got rid of. A
        soft-deleted row left behind would still read as "this type has photos"
        to whoever opens the admin next."""
        write(tmp_path, "DLX-1.png")
        run(hotel, directory=str(tmp_path))

        write(tmp_path, "DLX-2.png")
        run(hotel, directory=str(tmp_path), replace=True)

        rows = RoomTypePhoto.all_objects.filter(room_type__code="DLX")
        assert rows.count() == 2
        assert not rows.filter(is_deleted=True).exists()

    def test_the_replaced_files_go_too(self, hotel, types, tmp_path):
        """Django does not delete the file behind an ImageField. Replacing five
        times would otherwise leave five sets in media/ with nothing pointing at
        them, so nothing will ever clean them up."""
        from django.core.files.storage import default_storage

        write(tmp_path, "DLX-1.png")
        run(hotel, directory=str(tmp_path))
        stored = RoomTypePhoto.all_objects.get(room_type__code="DLX").image.name
        assert default_storage.exists(stored)

        run(hotel, directory=str(tmp_path), replace=True)

        assert not default_storage.exists(stored)


class TestRefusing:
    def test_a_source_is_required(self, hotel, types):
        with pytest.raises(CommandError, match="--dir"):
            run(hotel)

    def test_an_unknown_hotel_code(self, types):
        out = io.StringIO()
        with pytest.raises(CommandError, match="Unknown hotel code"):
            call_command("import_room_photos", hotel="NOPE-999", demo_set=True, stdout=out)

    def test_a_hotel_with_no_room_types_says_what_to_run_first(self, hotel, tmp_path):
        write(tmp_path, "DLX-1.png")
        with pytest.raises(CommandError, match="seed_pms"):
            run(hotel, directory=str(tmp_path))
