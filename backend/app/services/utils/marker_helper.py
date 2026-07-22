import re
from typing import  Optional
from .geometry_helper import (_horizontal_gap, _vertical_gap)
from .text_helper import _normalize_repeat_text
from .patterns import (
    MARKER_TEXTS,NEUTRAL_REPEATABLE_LABELS, EXCLUDED_SEMANTIC_LABELS,BAD_ALT_WORDS
)

def _collect_nearby_text(
    target_span: dict,
    page_spans: list[dict],
    max_h_gap: float = 90.0,
    max_v_gap: float = 20.0,
) -> list[dict]:
    tb = target_span.get("bbox")
    if not tb:
        return []

    out = []
    for sp in page_spans:
        if sp.get("id") == target_span.get("id"):
            continue

        sb = sp.get("bbox")
        st = (sp.get("text") or "").strip()
        if not sb or not st:
            continue

        h_gap = _horizontal_gap(tb, sb)
        v_gap = _vertical_gap(tb, sb)

        if h_gap <= max_h_gap and v_gap <= max_v_gap:
            out.append(sp)

    return out


def _is_marker_or_identical_label_candidate(text: str) -> bool:
    t = (text or "").strip()
    nt = _normalize_repeat_text(t)

    if not t:
        return False

    if t in MARKER_TEXTS:
        return True

    if nt in NEUTRAL_REPEATABLE_LABELS:
        return True

    if len(nt.split()) == 1 and len(nt) <= 8 and nt not in EXCLUDED_SEMANTIC_LABELS:
        return True

    return False


def _same_pattern_axis(
    b1: list[float],
    b2: list[float],
    x_tol: float = 24.0,
    y_tol: float = 16.0,
) -> bool:
    c1x = (b1[0] + b1[2]) / 2.0
    c1y = (b1[1] + b1[3]) / 2.0
    c2x = (b2[0] + b2[2]) / 2.0
    c2y = (b2[1] + b2[3]) / 2.0

    same_col = abs(c1x - c2x) <= x_tol
    same_row = abs(c1y - c2y) <= y_tol
    return same_col or same_row


def _is_descriptive_control_name(name: str | None) -> bool:
    if not isinstance(name, str):
        return False

    n = name.strip().lower()
    if not n:
        return False

    generic_patterns =  [
        r"^fld\d*$",
        r"^field\d*$",
        r"^text\d*$",
        r"^input\d*$",
        r"^textbox\d*$",
        r"^txt\d*$",
        r"^box\d*$",
        r"^form\d*$",
        r"^widget\d*$",
        r"^control\d*$",
        r"^button\d*$",
        r"^\d+$",
]

    for pattern in generic_patterns:
        if re.match(pattern, n):
            return False

    if len(n) < 3:
        return False

    return True


def _is_suspicious_alt_text(alt: Optional[str]) -> bool:
    if alt is None:
        return False

    if alt == "":
        return False

    alt_clean = alt.strip().lower()

    alt_unquoted = alt_clean.strip('"').strip("'").strip()

    if alt_unquoted in {"", '""', "''", "/n", "\\n", "n"}:
        return True

    if alt_unquoted in BAD_ALT_WORDS:
        return True

    if re.match(r".*\.(jpg|jpeg|png|gif|bmp|svg)$", alt_unquoted):
        return True

    if re.match(r"(img|image|scan|photo)[\-_]?\d+", alt_unquoted):
        return True

    if len(alt_unquoted) <= 2:
        return True

    return False

