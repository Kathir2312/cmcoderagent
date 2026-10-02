def order_line(name, cents):
    return f"{name}: ${cents // 100}.{cents % 100:02d}"


def total_line(cents):
    return f"Total: ${cents // 100}.{cents % 100:02d}"
