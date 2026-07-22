from typing import Any, Dict, List, Optional
from .geometry_helper import (center_of_bbox,_horizontal_gap, _vertical_gap)
import pikepdf

def matching_widget_for_acrofield(
    field: Dict[str, Any],
    widgets: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    field_name = (field.get("name") or "").strip().lower()
    field_page = field.get("page_index")

    if not field_name:
        return None

    if field_page is not None:
        for w in widgets:
            widget_name = (w.get("field_name") or "").strip().lower()
            if w.get("page_index") == field_page and widget_name == field_name:
                return w

    matches = []
    for w in widgets:
        widget_name = (w.get("field_name") or "").strip().lower()
        if widget_name == field_name:
            matches.append(w)

    if len(matches) == 1:
        return matches[0]

    return None


def collect_label(widget: Dict[str, Any], page_spans: List[Dict[str, Any]], max_h_gap: float = 140.0, max_v_gap: float = 35.0,
) -> List[Dict[str, Any]]:
    widget_bbox = widget.get("bbox")
    if not widget_bbox:
        return []

    candidates = []
    _, _, wx1, wy1 = widget_bbox
    wcx, wcy = center_of_bbox(widget_bbox)

    for sp in page_spans:
        sb = sp.get("bbox")
        st = (sp.get("text") or "").strip()
        if not sb or not st:
            continue

        h_gap = _horizontal_gap(widget_bbox, sb)
        v_gap = _vertical_gap(widget_bbox, sb)

        if h_gap <= max_h_gap and v_gap <= max_v_gap:
            _, _, sx1, sy1 = sb
            scx, scy = center_of_bbox(sb)

            is_left = sx1 <= wx1
            is_above = sy1 <= wy1

            candidates.append((
                0 if (is_left or is_above) else 1,
                v_gap,
                h_gap,
                abs(scy - wcy),
                abs(scx - wcx),
                sp
            ))

    candidates.sort(key=lambda x: x[:5])
    return [c[-1] for c in candidates[:5]]



def _collect_near_widget_label_spans(
    widget: dict,
    page_spans: list[dict],
    max_h_gap: float = 120.0,
    max_v_gap: float = 30.0,
) -> list[dict]:
    widget_bbox = widget.get("bbox")
    if not widget_bbox:
        return []

    out = []
    for sp in page_spans:
        sb = sp.get("bbox")
        st = (sp.get("text") or "").strip()
        if not sb or not st:
            continue

        h_gap = _horizontal_gap(widget_bbox, sb)
        v_gap = _vertical_gap(widget_bbox, sb)

        if h_gap <= max_h_gap and v_gap <= max_v_gap:
            out.append(sp)

    return out



def _read_mk_border_color_pikepdf(pdf_path: str, page_index: int, bbox: list, tolerance: float = 5.0) -> Optional[List[int]]:
    """
    Fallback: read border color from /MK /BC on the AcroForm field
    matching this widget by page + bbox. Returns RGB 0-255 or None.
    """
    try:
        with pikepdf.open(pdf_path) as pdf:
            acroform = pdf.Root.get("/AcroForm")
            if acroform is None:
                return None
            fields = acroform.get("/Fields")
            if not fields:
                return None

            page_height = float(pdf.pages[page_index].mediabox[3])
            # Convert PyMuPDF bbox to PDF coords for matching
            pdf_x0 = bbox[0]
            pdf_y0 = page_height - bbox[3]
            pdf_x1 = bbox[2]
            pdf_y1 = page_height - bbox[1]

            def close(a, b):
                return abs(a - b) < tolerance

            def check(node):
                if not isinstance(node, pikepdf.Dictionary):
                    try:
                        node = node.get_object()
                    except Exception:
                        return None
                # Check /Rect
                rect = node.get("/Rect")
                if rect is not None:
                    try:
                        r = [float(x) for x in rect]
                        if close(r[0], pdf_x0) and close(r[1], pdf_y0) and close(r[2], pdf_x1) and close(r[3], pdf_y1):
                            mk = node.get("/MK")
                            if isinstance(mk, pikepdf.Dictionary):
                                bc = mk.get("/BC")
                                if bc is not None and len(bc) >= 3:
                                    return [int(round(float(bc[i]) * 255)) for i in range(3)]
                            return None
                    except Exception:
                        pass
                kids = node.get("/Kids")
                if isinstance(kids, pikepdf.Array):
                    for kid in kids:
                        found = check(kid)
                        if found is not None:
                            return found
                return None

            for field_ref in fields:
                result = check(field_ref)
                if result is not None:
                    return result
    except Exception:
        pass
    return None
 