"""What shipping costs for an order."""

from __future__ import annotations

import math
from decimal import Decimal

from parcels.carriers import EU, TARIFFS
from parcels.packing import parcel_weights
from parcels.settings import CARRIER, FREE_SHIPPING_FROM


class CannotShip(ValueError):
    """The shop does not ship there."""


def parcel_price(weight_kg: Decimal, country: str) -> Decimal:
    tariff = TARIFFS[CARRIER]
    price = tariff.base + tariff.per_kg * math.ceil(weight_kg)
    if country != "NL":
        price += tariff.eu_surcharge
    return price


def shipping_cost(order_value: Decimal, weight_kg: Decimal, country: str) -> Decimal:
    """Shipping for an order worth ``order_value`` euros (after discounts) to ``country``."""

    country = country.upper()
    if country not in EU:
        raise CannotShip(f"we do not ship to {country}")
    if order_value >= FREE_SHIPPING_FROM:
        return Decimal("0.00")
    return sum((parcel_price(w, country) for w in parcel_weights(weight_kg)), Decimal("0.00"))
