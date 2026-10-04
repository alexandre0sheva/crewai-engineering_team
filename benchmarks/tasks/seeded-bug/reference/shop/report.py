"""The text report."""


def report(cart):
    average = cart.average_price()
    shown = "n/a" if average is None else f"{average:.2f}"
    return f"Items: {cart.count()}, total: {cart.total():.2f}, average: {shown}"
