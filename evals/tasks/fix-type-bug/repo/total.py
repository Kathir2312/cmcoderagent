def total(csv):
    result = ''
    for part in csv.split(','):
        result += part.strip()
    return result
