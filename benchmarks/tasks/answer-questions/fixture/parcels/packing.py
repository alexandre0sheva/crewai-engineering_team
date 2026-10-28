"""Splitting an order into parcels."""

from __future__ import annotations

import math
from decimal import Decimal

from parcels.settings import MAX_PARCEL_KG


def parcel_weights(total_kg: Decimal) -> list[Decimal]:
    """The weights of the parcels an order of ``total_kg`` is packed into.

    Heavy orders are split into as few parcels as possible, all of the same weight, each at most
    ``MAX_PARCEL_KG``: 70 kg becomes three parcels of 23.334 kg (rounded up to the gram).
    """

    if total_kg <= 0:
        raise ValueError("an order weighs something")
    count = max(1, math.ceil(total_kg / MAX_PARCEL_KG))
    each = (total_kg / count).quantize(Decimal("0.001"), rounding="ROUND_CEILING")
    return [each] * count
