def parse_duration(text):
    """Parse "1h30m", "45m" or "2h" into minutes."""
    hours, minutes = 0, 0
    if "h" in text:
        h, text = text.split("h", 1)
        hours = int(h)
    if "m" in text:
        minutes = int(text.rstrip("m"))
    return hours * 60 + minutes * 60
