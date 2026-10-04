"""The in-memory item store: the 'database' of this service."""

_items = {}
_next_id = [1]


def reset():
    _items.clear()
    _next_id[0] = 1


def add(name, qty, price):
    item = {"id": _next_id[0], "name": name, "qty": qty, "price": price}
    _items[item["id"]] = item
    _next_id[0] += 1
    return item


def all_items():
    return [_items[key] for key in sorted(_items)]


def get(item_id):
    return _items.get(item_id)


def remove(item_id):
    return _items.pop(item_id, None)
