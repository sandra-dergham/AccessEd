from typing import Any, Dict,List


def similar_font_properties(
    font1: Dict[str, Any],
    font2: Dict[str, Any],
    size_tol: float = 1.0
) -> bool:
    same_name = font1.get("name") == font2.get("name")
    similar_size = abs(float(font1.get("size", 0.0)) - float(font2.get("size", 0.0))) <= size_tol

    flags1 = font1.get("flags")
    flags2 = font2.get("flags")

    flags_close = True
    if flags1 is not None and flags2 is not None:
        flags_close = flags1 == flags2

    return same_name and similar_size and flags_close



def detect_heading_candidates(text_spans: List[Dict[str, Any]]) -> List[str]:
    """
    Return IDs of spans that look like headings based on visual heuristics.
    """
    font_sizes = []
    for span in text_spans:
        size = span.get("font", {}).get("size")
        if isinstance(size, (int, float)) and size > 0:
            font_sizes.append(size)

    avg_font_size = sum(font_sizes) / len(font_sizes) if font_sizes else 0
    candidates = []

    for span in text_spans:
        text = (span.get("text") or "").strip()
        font = span.get("font", {})
        size = font.get("size", 0)
        font_name = str(font.get("name", "")).lower()

        if not text:
            continue
        if len(text) > 60:
            continue
        if not any(ch.isalpha() for ch in text):
            continue

        stripped_alnum = "".join(ch for ch in text if ch.isalnum())
        if stripped_alnum.isdigit():
            continue

        is_large   = size >= max(16, avg_font_size * 1.2)
        is_boldish = any(word in font_name for word in ["bold", "black", "semibold", "demi"])

        if is_large or (is_boldish and size >= avg_font_size):
            candidates.append(span["id"])

    return candidates



