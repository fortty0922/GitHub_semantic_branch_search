def english_ratio(text: str) -> float:
    """Share of ASCII letters among all letters; 0 when the text has no letters."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum(c < "\x80" for c in letters) / len(letters)
