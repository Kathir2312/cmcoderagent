"""Ledger entries for invoices and refunds."""

from .charges import apply_levy, shipping_fee


def invoice_total(net_amount, weight_kg):
    return net_amount + apply_levy(net_amount) + shipping_fee(weight_kg)


def refund(entry):
    return {"amount": -entry["amount"], "reason": entry.get("reason", "refund")}
