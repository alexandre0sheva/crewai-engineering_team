"""Order summaries for the monthly report."""

TAX_RATES = {"US": 0.07, "DE": 0.19, "FR": 0.2}


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
        if not valid and order.get("strict"):
            problems.append("order %s: skipped (strict)" % order.get("id"))
            continue
        coupon = order.get("coupon")
        discount = 0.0
        if coupon == "TEN" and subtotal >= 100:
            discount = subtotal * 0.10
        elif coupon == "FIVE":
            discount = min(5.0, subtotal)
        elif coupon == "HALF" and order.get("customer") == "staff":
            discount = subtotal * 0.5
        taxable = subtotal - discount
        country = order.get("country", "US")
        tax = round(taxable * TAX_RATES.get(country, 0.0), 2)
        if taxable >= 50:
            shipping = 0.0
        elif country != "US":
            shipping = 9.99
        else:
            shipping = 4.99
        total = round(taxable + tax + shipping, 2)
        customer = order.get("customer", "unknown")
        entry = totals.setdefault(customer, {"orders": 0, "spent": 0.0})
        entry["orders"] += 1
        entry["spent"] = round(entry["spent"] + total, 2)
        revenue += total
    top_sku = None
    if sku_units:
        top_sku = sorted(sku_units.items(), key=lambda pair: (-pair[1], str(pair[0])))[0][0]
    best = None
    for customer, entry in totals.items():
        if best is None or entry["spent"] > totals[best]["spent"]:
            best = customer
    return {
        "revenue": round(revenue, 2),
        "customers": totals,
        "status_counts": status_counts,
        "top_sku": top_sku,
        "best_customer": best,
        "problems": problems,
    }
