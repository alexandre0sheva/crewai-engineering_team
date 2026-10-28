"""Carrier price tables: a base price per parcel and a price per started kilogram."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Tariff:
    base: Decimal
    per_kg: Decimal
    eu_surcharge: Decimal  # added for a destination outside the Netherlands, inside the EU


TARIFFS = {
    "dhl": Tariff(Decimal("4.95"), Decimal("0.40"), Decimal("6.00")),
    "ups": Tariff(Decimal("6.50"), Decimal("0.30"), Decimal("5.00")),
    "postnl": Tariff(Decimal("4.10"), Decimal("0.45"), Decimal("7.25")),
}

EU = frozenset(
    "AT BE BG HR CY CZ DK EE FI FR DE GR HU IE IT LV LT LU MT NL PL PT RO SK SI ES SE".split()
)
