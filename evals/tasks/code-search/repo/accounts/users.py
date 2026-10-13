"""User accounts: sign-up and password changes."""


def sign_up(email, password):
    if len(password) < 12:
        raise ValueError("password too short")
    return {"email": email.lower(), "active": True}


def change_password(user, old, new):
    if old == new:
        raise ValueError("the new password must differ")
    user["password_changed"] = True
    return user
