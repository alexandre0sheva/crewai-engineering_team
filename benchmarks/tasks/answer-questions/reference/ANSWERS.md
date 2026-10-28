# Answers about the parcels code

## Q1

Shipping is free for orders worth **60.00 euros or more** (after discounts), inside the EU. The
threshold is `FREE_SHIPPING_FROM` (parcels/settings.py:14-15), read from the environment variable
`PARCELS_FREE_FROM` at import, so it can be changed without editing code. `shipping_cost` returns
0.00 at or above it (parcels/quote.py:31-32).

## Q2

It is split into as few parcels as possible, all of the same weight, none heavier than 31.5 kg
(`MAX_PARCEL_KG`, parcels/settings.py:17). `parcel_weights` divides the total evenly and rounds each
parcel up to the gram, so 70 kg becomes three parcels of 23.334 kg (parcels/packing.py:11-22). Each
parcel is then priced on its own (parcels/quote.py:33).

## Q3

PostNL (`postnl`) is the default. Another carrier is chosen with the environment variable
`PARCELS_CARRIER` (`dhl`, `ups` or `postnl`); any other value stops the program at import with an
error (parcels/settings.py:8-12).

## Q4

An order placed at or after 15:00 (`CUTOFF_HOUR`) is handled from the next day, so a Friday 17:00
order starts on Saturday (parcels/delivery.py:11 and parcels/delivery.py:27-28). Then one handling
day plus the country's transit days (1 for NL, 2 for BE/DE/LU, 4 elsewhere) are added as working
days: Saturdays and Sundays are skipped, public holidays are not (parcels/delivery.py:14-22,
parcels/delivery.py:29-30). For the Netherlands that is Tuesday.
