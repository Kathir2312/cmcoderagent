"""Order storage."""


def load_orders(path, limit=250):
    with open(path, encoding="utf-8") as f:
        return [line.strip() for line in f][:limit]
