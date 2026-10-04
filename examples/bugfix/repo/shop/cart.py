"""A shopping cart."""


class Cart:
    def __init__(self):
        self.lines = []

    def add(self, name, price, qty=1):
        if price < 0 or qty < 1:
            raise ValueError("price must not be negative and qty must be at least 1")
        self.lines.append((name, price, qty))

    def count(self):
        """The number of items (a line of 3 counts 3)."""
        return sum(qty for _, _, qty in self.lines)

    def total(self):
        return round(sum(price * qty for _, price, qty in self.lines), 2)

    def average_price(self):
        """The mean price per item."""
        return round(self.total() / self.count(), 2)
