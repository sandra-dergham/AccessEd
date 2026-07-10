from typing import Any, Dict, List, Optional, Tuple


def bbox_width(bbox: List[float]) -> float:
    return max(0.0, bbox[2] - bbox[0])


def bbox_height(bbox: List[float]) -> float:
    return max(0.0, bbox[3] - bbox[1])


def scale_bbox_from_center(
    bbox: List[float],
    scale_x: float = 2.0,
    scale_y: float = 2.0
) -> List[float]:
    x0, y0, x1, y1 = bbox
    cx = (x0 + x1) / 2.0
    cy = (y0 + y1) / 2.0

    new_w = (x1 - x0) * scale_x
    new_h = (y1 - y0) * scale_y

    return [
        cx - new_w / 2.0,
        cy - new_h / 2.0,
        cx + new_w / 2.0,
        cy + new_h / 2.0,
    ]


def bbox_contains(outer: List[float], inner: List[float], margin: float = 0.0) -> bool:
    return (
        outer[0] - margin <= inner[0] and
        outer[1] - margin <= inner[1] and
        outer[2] + margin >= inner[2] and
        outer[3] + margin >= inner[3]
    )


def center_of_bbox(b: List[float]) -> Tuple[float, float]:
    x0, y0, x1, y1 = b
    return ((x0 + x1) / 2.0, (y0 + y1) / 2.0)


def bbox_area(bbox: Optional[List[float]]) -> float:
    if not bbox or len(bbox) != 4:
        return 0.0
    x0, y0, x1, y1 = bbox
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)


def bbox_intersection(b1: List[float], b2: List[float]) -> Optional[List[float]]:
    x0 = max(b1[0], b2[0])
    y0 = max(b1[1], b2[1])
    x1 = min(b1[2], b2[2])
    y1 = min(b1[3], b2[3])

    if x1 <= x0 or y1 <= y0:
        return None
    return [x0, y0, x1, y1]


def bbox_expand(bbox: List[float], margin: float) -> List[float]:
    x0, y0, x1, y1 = bbox
    return [x0 - margin, y0 - margin, x1 + margin, y1 + margin]


def intersection_ratio_of_span(span_bbox: List[float], other_bbox: List[float]) -> float:
    inter = bbox_intersection(span_bbox, other_bbox)
    if inter is None:
        return 0.0
    span_a = bbox_area(span_bbox)
    if span_a <= 0:
        return 0.0
    return bbox_area(inter) / span_a


def bbox_intersects(b1: List[float], b2: List[float], margin: float = 0.0) -> bool:
    bb1 = bbox_expand(b1, margin) if margin > 0 else b1
    return bbox_intersection(bb1, b2) is not None


def _horizontal_gap(b1: list[float], b2: list[float]) -> float:
    return max(b1[0] - b2[2], b2[0] - b1[2], 0.0)


def _vertical_gap(b1: list[float], b2: list[float]) -> float:
    return max(b1[1] - b2[3], b2[1] - b1[3], 0.0)


def _union_bboxes(boxes: list[list[float]]) -> list[float] | None:
    if not boxes:
        return None
    return [
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    ]

def bbox_exceeds_container(candidate_bbox: List[float], container: List[float], margin: float = 1.0) -> bool:
    return not bbox_contains(container, candidate_bbox, margin=margin)



def estimate_resized_text_bbox_200(span_bbox: List[float]) -> List[float]:
    x0, y0, x1, y1 = span_bbox
    w = max(0.0, x1 - x0)
    h = max(0.0, y1 - y0)

    # Heuristic 200% enlargement:
    # width grows more strongly than height, with slight left/up padding
    return [
        x0 - 0.10 * w,
        y0 - 0.20 * h,
        x0 + 2.00 * w,
        y0 + 1.80 * h,
    ]


def bbox_exceeds_container(candidate_bbox: List[float], container: List[float], margin: float = 1.0) -> bool:
    return not bbox_contains(container, candidate_bbox, margin=margin)



def looks_like_paragraph_continuation(span1: Dict[str, Any], span2: Dict[str, Any]) -> bool:
    from .text_helper import is_same_line
    if span1.get("page_index") != span2.get("page_index"):
        return False

    b1 = span1.get("bbox")
    b2 = span2.get("bbox")
    if not b1 or not b2:
        return False

    # same line => not paragraph continuation
    if is_same_line(span1, span2, y_tol=8.0):
        return False

    # vertically near
    v_gap = _vertical_gap(b1, b2)

    # left aligned or strongly overlapping in x => likely wrapped paragraph lines
    left_aligned = abs(b1[0] - b2[0]) <= 25.0

    overlap = max(0.0, min(b1[2], b2[2]) - max(b1[0], b2[0]))
    min_width = min(bbox_width(b1), bbox_width(b2))
    overlap_ratio = (overlap / min_width) if min_width > 0 else 0.0

    return v_gap <= 14.0 and (left_aligned or overlap_ratio >= 0.4)


def find_line_box_container(
    span_bbox: List[float],
    page_graphics: List[Dict[str, Any]],
    tolerance: float = 3.0
) -> Optional[List[float]]:
    """
    Detect a rectangular box around text when the box is drawn using 4 separate line graphics.
    """
    x0, y0, x1, y1 = span_bbox

    horizontals = []
    verticals = []

    for g in page_graphics:
        gb = g.get("bbox")
        if not gb or g.get("type") != "s":
            continue

        w = bbox_width(gb)
        h = bbox_height(gb)

        if h <= tolerance and w > 20:
            horizontals.append(gb)

        if w <= tolerance and h > 20:
            verticals.append(gb)

    top_candidates = []
    bottom_candidates = []
    left_candidates = []
    right_candidates = []

    for hb in horizontals:
        hx0, hy0, hx1, hy1 = hb
        if hx0 <= x0 + tolerance and hx1 >= x1 - tolerance:
            cy = (hy0 + hy1) / 2.0
            if cy <= y0 + tolerance:
                top_candidates.append(hb)
            if cy >= y1 - tolerance:
                bottom_candidates.append(hb)

    for vb in verticals:
        vx0, vy0, vx1, vy1 = vb
        if vy0 <= y0 + tolerance and vy1 >= y1 - tolerance:
            cx = (vx0 + vx1) / 2.0
            if cx <= x0 + tolerance:
                left_candidates.append(vb)
            if cx >= x1 - tolerance:
                right_candidates.append(vb)

    if not (top_candidates and bottom_candidates and left_candidates and right_candidates):
        return None

    top = min(top_candidates, key=lambda b: abs(((b[1] + b[3]) / 2.0) - y0))
    bottom = min(bottom_candidates, key=lambda b: abs(((b[1] + b[3]) / 2.0) - y1))
    left = min(left_candidates, key=lambda b: abs(((b[0] + b[2]) / 2.0) - x0))
    right = min(right_candidates, key=lambda b: abs(((b[0] + b[2]) / 2.0) - x1))

    container = [
        min(left[0], left[2]),
        min(top[1], top[3]),
        max(right[0], right[2]),
        max(bottom[1], bottom[3]),
    ]

    if bbox_contains(container, span_bbox, margin=2.0):
        return container

    return None

def pdfminer_bbox_to_pymupdf_bbox(bbox_pdfminer: List[float], page_height: float) -> List[float]:
    """
    pdfminer bbox: [x0, y0, x1, y1] with origin bottom-left.
    Convert to PyMuPDF-like: y grows downward (top-left style).
    """
    x0, y0, x1, y1 = bbox_pdfminer
    new_y0 = page_height - y1
    new_y1 = page_height - y0
    return [float(x0), float(new_y0), float(x1), float(new_y1)]


def center_of_bbox(b: List[float]) -> Tuple[float, float]:
    x0, y0, x1, y1 = b
    return ((x0 + x1) / 2.0, (y0 + y1) / 2.0)


def point_in_bbox(px: float, py: float, b: List[float], margin: float = 2.0) -> bool:
    x0, y0, x1, y1 = b
    return (x0 - margin) <= px <= (x1 + margin) and (y0 - margin) <= py <= (y1 + margin)


def align_blocks_to_spans(
    text_blocks: List[Dict[str, Any]],
    text_spans:  List[Dict[str, Any]],
    pages:       List[Dict[str, Any]],
):
    """
    Fill each text_blocks[i]["span_ids"] by matching span centers into block bbox.
    Also adds "bbox" for the block in PyMuPDF coordinates.
    """
    spans_by_page: Dict[int, List[Dict[str, Any]]] = {}
    for s in text_spans:
        spans_by_page.setdefault(s["page_index"], []).append(s)

    page_height: Dict[int, float] = {
        p["page_index"]: float(p["height"]) for p in pages
    }

    for blk in text_blocks:
        pno = blk["page_index"]
        h   = page_height.get(pno)
        if h is None:
            continue

        bbox_pym   = pdfminer_bbox_to_pymupdf_bbox(blk["bbox_pdfminer"], h)
        blk["bbox"] = bbox_pym

        candidates  = spans_by_page.get(pno, [])
        matched_ids = []

        for sp in candidates:
            cx, cy = center_of_bbox(sp["bbox"])
            if point_in_bbox(cx, cy, bbox_pym, margin=3.0):
                matched_ids.append(sp["id"])

        def span_sort_key(span_id: str):
            s = next((x for x in candidates if x["id"] == span_id), None)
            if not s:
                return (0, 0)
            x0, y0, x1, y1 = s["bbox"]
            return (y0, x0)

        matched_ids.sort(key=span_sort_key)
        blk["span_ids"] = matched_ids

    return text_blocks

