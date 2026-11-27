def solve(items):
    """Reference implementation for the measured optimization task."""
    seen = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return len(seen)
