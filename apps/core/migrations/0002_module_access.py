"""Module-access permissions for the top-level navigation.

Creates Permission rows only — ModuleAccess is managed=False, so no table is
added. See apps/core/models.py for why menu gating uses coarse module
permissions instead of model permissions.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    initial = False

    dependencies = [
        ('core', '0001_enable_pgvector'),
    ]

    operations = [
        migrations.CreateModel(
            name='ModuleAccess',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ],
            options={
                'verbose_name': 'module access',
                'verbose_name_plural': 'module access',
                'permissions': [('access_reception', 'Can open AI Reception'), ('access_guests', 'Can open Guests'), ('access_rooms', 'Can open Rooms & Inventory'), ('access_reservations', 'Can open Reservations'), ('access_housekeeping', 'Can open Housekeeping'), ('access_restaurant', 'Can open Restaurant & POS'), ('access_billing', 'Can open Billing & Finance'), ('access_ai_center', 'Can open AI Center'), ('access_reports', 'Can open Reports & Analytics'), ('access_settings', 'Can open Settings')],
                'managed': False,
                'default_permissions': (),
            },
        ),
    ]
