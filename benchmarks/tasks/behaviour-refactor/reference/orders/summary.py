"""Order summaries for the monthly report."""

TAX_RATES = {"US": 0.07, "DE": 0.19, "FR": 0.2}


def _subtotal(order, sku_units, problems):
    """The order's item total; records bad items and counts units per SKU. (subtotal, valid)"""
    subtotal = 0.0
    valid = True
    for item in order.get("items", []):
        qty = item.get("qty", 0)
        price = item.get("price", 0.0)
        if qty <= 0 or price < 0:
            problems.append("order %s: bad item %s" % (order.get("id"), item.get("sku")))
            valid = False
            continue
        subtotal += qty * price
        sku = item.get("sku")
        sku_units[sku] = sku_units.get(sku, 0) + qty
    return subtotal, valid


def _discount(order, subtotal):
    coupon = order.get("coupon")
    if coupon == "TEN" and subtotal >= 100:
        return subtotal * 0.10
    if coupon == "FIVE":
        return min(5.0, subtotal)
    if coupon == "HALF" and order.get("customer") == "staff":
        return subtotal * 0.5
    return 0.0


def _shipping(country, taxable):
    if taxable >= 50:
        return 0.0
    return 9.99 if country != "US" else 4.99


def _order_total(order, subtotal):
    taxable = subtotal - _discount(order, subtotal)
    country = order.get("country", "US")
    tax = round(taxable * TAX_RATES.get(country, 0.0), 2)
    return round(taxable + tax + _shipping(country, taxable), 2)


def _top_sku(sku_units):
    if not sku_units:
        return None
    return sorted(sku_units.items(), key=lambda pair: (-pair[1], str(pair[0])))[0][0]


def _best_customer(totals):
    best = None
    for customer, entry in totals.items():
        if best is None or entry["spent"] > totals[best]["spent"]:
            best = customer
    return best


def _record(totals, customer, total):
    entry = totals.setdefault(customer, {"orders": 0, "spent": 0.0})
    entry["orders"] += 1
    entry["spent"] = round(entry["spent"] + total, 2)


def summarize(orders):
    """Summarize a list of order dicts into revenue, per-customer totals and counts."""
    totals = {}
    status_counts = {}
    sku_units = {}
    revenue = 0.0
    problems = []
    for order in orders:
        status = order.get("status", "new")
        status_counts[status] = status_counts.get(status, 0) + 1
        if status == "cancelled":
            continue
        subtotal, valid = _subtotal(order, sku_units, problems)
        if not valid and order.get("strict"):
            problems.append("order %s: skipped (strict)" % order.get("id"))
            continue
        total = _order_total(order, subtotal)
        _record(totals, order.get("customer", "unknown"), total)
        revenue += total
    return {
        "revenue": round(revenue, 2),
        "customers": totals,
        "status_counts": status_counts,
        "top_sku": _top_sku(sku_units),
        "best_customer": _best_customer(totals),
        "problems": problems,
    }
