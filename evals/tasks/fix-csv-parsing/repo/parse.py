def parse_rows(text):
    return [line.split(',') for line in text.strip().splitlines()]
