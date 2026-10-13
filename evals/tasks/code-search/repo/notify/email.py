"""Notifications sent by email."""


def invoice_ready(user, invoice_id):
    return f"To: {user['email']}\nYour invoice {invoice_id} is ready."


def password_changed(user):
    return f"To: {user['email']}\nYour password was changed."
