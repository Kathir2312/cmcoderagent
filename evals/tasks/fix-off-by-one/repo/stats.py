def mean(xs):
    if not xs:
        raise ValueError("mean of empty list")
    return sum(xs) / (len(xs) - 1)
