from typing import Any, Dict, List, Optional
from .geometry_helper import bbox_intersects
from .text_helper import is_same_line

def span_intersects_any_link(
    span_bbox: List[float],
    links: List[Dict[str, Any]],
    margin: float = 2.0
) -> Optional[Dict[str, Any]]:
    for link in links:
        lb = link.get("bbox")
        if not lb:
            continue
        if bbox_intersects(span_bbox, lb, margin=margin):
            return link
    return None


def collect_same_line_non_link_neighbors(
    target_span: Dict[str, Any],
    text_spans: List[Dict[str, Any]],
    page_links: List[Dict[str, Any]],
    max_horizontal_distance: float = 220.0,
) -> List[Dict[str, Any]]:
    out = []
    tb = target_span.get("bbox")
    if not tb:
        return out

    for other in text_spans:
        if other.get("id") == target_span.get("id"):
            continue
        if other.get("page_index") != target_span.get("page_index"):
            continue

        ob = other.get("bbox")
        if not ob:
            continue

        if not is_same_line(target_span, other):
            continue

        if span_intersects_any_link(ob, page_links, margin=2.0):
            continue

        out.append(other)

    return out
