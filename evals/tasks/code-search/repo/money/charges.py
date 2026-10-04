"""Amounts added on top of an invoice's net total."""

LEVY_RATE = 0.25  # value added tax


def apply_levy(net_amount):
    """The value added tax owed on a net amount."""
    return round(net_amount * LEVY_RATE, 2)


def shipping_fee(weight_kg):
    """Flat shipping fee by weight band."""
    return 5 if weight_kg < 2 else 12
