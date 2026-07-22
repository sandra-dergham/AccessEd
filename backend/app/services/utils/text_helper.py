from typing import Any, Dict, List, Optional
from .geometry_helper import center_of_bbox
import re
from langdetect import detect, LangDetectException

def is_url_like_text(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return False
    return (
        "http://" in t
        or "https://" in t
        or t.startswith("www.")
        or "@" in t
    )


def is_same_line(span1: Dict[str, Any], span2: Dict[str, Any], y_tol: float = 6.0) -> bool:
    l1 = span1.get("layout", {})
    l2 = span2.get("layout", {})

    if (
        l1.get("block_index") is not None and
        l1.get("line_index") is not None and
        l2.get("block_index") is not None and
        l2.get("line_index") is not None
    ):
        return (
            l1.get("block_index") == l2.get("block_index")
            and l1.get("line_index") == l2.get("line_index")
            and span1.get("page_index") == span2.get("page_index")
        )

    b1 = span1.get("bbox")
    b2 = span2.get("bbox")
    if not b1 or not b2:
        return False

    _, c1y = center_of_bbox(b1)
    _, c2y = center_of_bbox(b2)
    return abs(c1y - c2y) <= y_tol


def _sort_spans_reading_order(spans: list[dict]) -> list[dict]:
    def sort_key(span: dict):
        layout = span.get("layout", {})
        bbox = span.get("bbox", [0.0, 0.0, 0.0, 0.0])

        block_index = layout.get("block_index")
        line_index = layout.get("line_index")

        if block_index is not None and line_index is not None:
            return (0, block_index, line_index, bbox[0], bbox[1])

        return (1, bbox[1], bbox[0], bbox[3], bbox[2])

    return sorted(spans, key=sort_key)




def _normalize_repeat_text(text: str) -> str:
    return " ".join((text or "").strip().lower().split())

def normalize_label(text: Optional[str]) -> str:
    if not text:
        return ""
    text = text.strip().lower()
    text = re.sub(r"[_\-]+", " ", text)      
    text = re.sub(r"[^\w\s]", "", text)      
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def combine_nearby_spans(
    spans: List[Dict[str, Any]],
    limit: int = 3,
    y_tol: float = 8.0
) -> str:
    if not spans:
        return ""

    first = spans[0]
    first_bbox = first.get("bbox")
    if not first_bbox:
        return ""

    _, first_cy = center_of_bbox(first_bbox)

    same_line_parts = []
    for sp in spans[:limit]:
        sb = sp.get("bbox")
        txt = (sp.get("text") or "").strip()
        if not sb or not txt:
            continue

        _, cy = center_of_bbox(sb)
        if abs(cy - first_cy) <= y_tol:
            same_line_parts.append((sb[0], txt))

    same_line_parts.sort(key=lambda x: x[0])
    return " ".join(txt for _, txt in same_line_parts).strip()



def detect_language_safe(text: str) -> Optional[str]:
    """
    Detect language of a text snippet.
    Returns ISO language code or None if detection fails.
    """
    try:
        if not text or len(text.strip()) < 10:
            return None
        return detect(text)
    except LangDetectException:
        return None


def infer_document_language(text_spans: List[Dict[str, Any]]) -> Optional[str]:
    """
    Infer the dominant document language from detected span languages.
    Returns a language code only if one language clearly dominates.
    """
    from collections import Counter

    langs = []
    for span in text_spans:
        lang = span.get("detected_language")
        text = (span.get("text") or "").strip()

        if not lang or not text:
            continue
        if len(text) < 20:
            continue
        if not any(ch.isalpha() for ch in text):
            continue

        langs.append(str(lang).lower().split("-")[0])

    if not langs:
        return None

    counts = Counter(langs)
    top_lang, top_count = counts.most_common(1)[0]

    if top_count / len(langs) >= 0.8:
        return top_lang

    return None



