"""Settings read from the environment once, at import."""

from __future__ import annotations

import os
from decimal import Decimal

CARRIERS = ("dhl", "ups", "postnl")

CARRIER = os.environ.get("PARCELS_CARRIER", "postnl").lower()
if CARRIER not in CARRIERS:
    raise RuntimeError(f"PARCELS_CARRIER must be one of {', '.join(CARRIERS)}, not {CARRIER!r}")

# Orders at or above this value (in euros, after discounts) ship for free inside the EU.
FREE_SHIPPING_FROM = Decimal(os.environ.get("PARCELS_FREE_FROM", "60.00"))

MAX_PARCEL_KG = Decimal("31.5")
HANDLING_DAYS = 1
