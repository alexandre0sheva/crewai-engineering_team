"""The text report."""


def report(cart):
    average = cart.average_price()
    return f"Items: {cart.count()}, total: {cart.total():.2f}, average: {average:.2f}"
