from .store import load_orders


def recent(path):
    return load_orders(path, limit=10)
