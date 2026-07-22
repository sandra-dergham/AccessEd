from typing import Any, Dict, List, Optional
import fitz
import numpy as np
import pikepdf
from .geometry_helper import (bbox_width, bbox_height, bbox_contains, bbox_intersects)

def has_underline_graphic(
    span_bbox: List[float],
    page_graphics: List[Dict[str, Any]],
    vertical_tol: float = 3.0,
    max_line_height: float = 3.0,
    min_width_ratio: float = 0.6,
) -> bool:
    sx0, sy0, sx1, sy1 = span_bbox
    span_w = bbox_width(span_bbox)

    for g in page_graphics:
        gb = g.get("bbox")
        if not gb:
            continue

        gx0, gy0, gx1, gy1 = gb
        gw = bbox_width(gb)
        gh = bbox_height(gb)

        if gh > max_line_height:
            continue
        if gw < span_w * min_width_ratio:
            continue

        horizontally_aligned = not (gx1 < sx0 or gx0 > sx1)
        if not horizontally_aligned:
            continue

        if abs(gy0 - sy1) <= vertical_tol or abs(((gy0 + gy1) / 2.0) - sy1) <= vertical_tol:
            return True

    return False


def has_enclosing_box_cue(
    span_bbox: List[float],
    page_graphics: List[Dict[str, Any]],
    margin: float = 2.0,
    max_extra_scale: float = 1.8,
) -> bool:
    span_w = bbox_width(span_bbox)
    span_h = bbox_height(span_bbox)

    for g in page_graphics:
        gb = g.get("bbox")
        if not gb:
            continue

        if bbox_contains(gb, span_bbox, margin=margin):
            gw = bbox_width(gb)
            gh = bbox_height(gb)

            tight_w = gw <= span_w * max_extra_scale
            tight_h = gh <= span_h * max_extra_scale

            if tight_w and tight_h:
                return True

    return False

        
def graphic_overlaps_widget(graphic: Dict[str, Any], widgets: List[Dict[str, Any]], margin: float = 2.0) -> bool:
    gb = graphic.get("bbox")
    if not gb:
        return False

    for w in widgets:
        wb = w.get("bbox")
        if not wb:
            continue
        if bbox_intersects(gb, wb, margin=margin):
            return True
    return False


def is_likely_layout_or_decorative_graphic(
    graphic: Dict[str, Any],
    page_width: float,
    page_height: float
) -> bool:
    bbox = graphic.get("bbox")
    if not bbox:
        return True

    w = bbox_width(bbox)
    h = bbox_height(bbox)
    area = w * h
    page_area = page_width * page_height if page_width > 0 and page_height > 0 else 0

    fill_rgb = graphic.get("fill_rgb")
    gtype = graphic.get("type")

    # 1) huge/light panel backgrounds
    if page_area > 0 and area / page_area >= 0.025:
        if fill_rgb and all(c >= 235 for c in fill_rgb[:3]):
            return True

    # 2) white or near-white helper boxes inside panels
    if fill_rgb and all(c >= 245 for c in fill_rgb[:3]):
        if area >= 1500:
            return True

    # 3) long thin separator lines
    if gtype == "s":
        if (h <= 2.0 and w >= page_width * 0.5) or (w <= 2.0 and h >= page_height * 0.5):
            return True

    # 4) very light filled rectangles used as containers/cards
    if gtype == "f" and fill_rgb and all(c >= 240 for c in fill_rgb[:3]):
        if w >= 150 and h >= 40:
            return True

    return False


def sample_widget_border_rgb(
    page: fitz.Page,
    bbox: List[float],
    scale: float = 2.0,
    edge_px: int = 2
) -> Optional[List[int]]:
    img, scale = render_page_to_array(page, scale=scale)
    if img is None:
        return None

    px0, py0, px1, py1 = bbox_to_pixel_rect(bbox, img.shape, scale)
    if px1 <= px0 or py1 <= py0:
        return None

    samples = []

    top = img[py0:min(py0 + edge_px, py1), px0:px1]
    if top.size:
        samples.append(top.reshape(-1, img.shape[2]))

    bottom = img[max(py0, py1 - edge_px):py1, px0:px1]
    if bottom.size:
        samples.append(bottom.reshape(-1, img.shape[2]))

    left = img[py0:py1, px0:min(px0 + edge_px, px1)]
    if left.size:
        samples.append(left.reshape(-1, img.shape[2]))

    right = img[py0:py1, max(px0, px1 - edge_px):px1]
    if right.size:
        samples.append(right.reshape(-1, img.shape[2]))

    if not samples:
        return None

    pixels = np.vstack(samples)
    return median_rgb_from_pixels(pixels) 

 

def sample_outside_ring_rgb(
    page: fitz.Page,
    bbox: List[float],
    scale: float = 2.0,
    ring_px: int = 4
) -> Optional[List[int]]:
    img, scale = render_page_to_array(page, scale=scale)
    if img is None:
        return None

    px0, py0, px1, py1 = bbox_to_pixel_rect(bbox, img.shape, scale)
    if px1 <= px0 or py1 <= py0:
        return None

    h, w = img.shape[:2]

    ox0 = max(0, px0 - ring_px)
    oy0 = max(0, py0 - ring_px)
    ox1 = min(w - 1, px1 + ring_px)
    oy1 = min(h - 1, py1 + ring_px)

    samples = []

    if oy0 < py0:
        top = img[oy0:py0, ox0:ox1]
        if top.size:
            samples.append(top.reshape(-1, img.shape[2]))

    if py1 < oy1:
        bottom = img[py1:oy1, ox0:ox1]
        if bottom.size:
            samples.append(bottom.reshape(-1, img.shape[2]))

    if ox0 < px0:
        left = img[py0:py1, ox0:px0]
        if left.size:
            samples.append(left.reshape(-1, img.shape[2]))

    if px1 < ox1:
        right = img[py0:py1, px1:ox1]
        if right.size:
            samples.append(right.reshape(-1, img.shape[2]))

    if not samples:
        return None

    pixels = np.vstack(samples)
    return median_rgb_from_pixels(pixels)


def render_page_to_array(page: fitz.Page, scale: float = 2.0):
    mat = fitz.Matrix(scale, scale)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    if pix.n < 3:
        return None, None
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    return img, scale


def bbox_to_pixel_rect(bbox: List[float], img_shape, scale: float):
    h, w = img_shape[:2]
    x0, y0, x1, y1 = bbox
    px0 = int(max(0, min(w - 1, round(x0 * scale))))
    py0 = int(max(0, min(h - 1, round(y0 * scale))))
    px1 = int(max(0, min(w - 1, round(x1 * scale))))
    py1 = int(max(0, min(h - 1, round(y1 * scale))))
    return px0, py0, px1, py1


def median_rgb_from_pixels(pixels: np.ndarray) -> Optional[List[int]]:
    if pixels is None or len(pixels) == 0:
        return None
    med = np.median(pixels[:, :3], axis=0)
    return [int(med[0]), int(med[1]), int(med[2])] 


def map_single_figure_alt_to_single_image(
    structure:         Dict[str, Any],
    image_occurrences: List[Dict[str, Any]]
) -> None:
    figures = structure.get("figures", [])
    if len(figures) != 1 or len(image_occurrences) != 1:
        return

    fig = figures[0]
    occ = image_occurrences[0]

    alt         = fig.get("alt")
    actual_text = fig.get("actual_text")
    text_alt    = alt if alt is not None else actual_text

    if occ.get("alt_source") is not None:
        return

    occ["alt_text"]         = text_alt
    occ["alt_source"]       = "structure_single_match"
    occ["struct_figure_id"] = fig.get("id")


def map_figures_to_images_by_order(
    structure:         Dict[str, Any],
    image_occurrences: List[Dict[str, Any]]
) -> None:
    figures = structure.get("figures", [])
    if not figures or len(figures) != len(image_occurrences):
        return

    for fig, occ in zip(figures, image_occurrences):
        alt         = fig.get("alt")
        actual_text = fig.get("actual_text")
        text_alt    = alt if alt is not None else actual_text

        if occ.get("alt_source") is not None:
            continue

        occ["alt_text"]         = text_alt
        occ["alt_source"]       = "structure_order_match"
        occ["struct_figure_id"] = fig.get("id")


def attach_page_index_to_figures(figures: List[Dict[str, Any]], pdf_path: str) -> None:
    try:
        with pikepdf.open(pdf_path) as pdf:
            objgen_to_page_index = {}
            for i, page in enumerate(pdf.pages):
                try:
                    objgen_to_page_index[page.objgen] = i
                except Exception:
                    pass

            for fig in figures:
                fig["page_index"] = None
                ref = fig.get("page_object_ref")
                if ref in objgen_to_page_index:
                    fig["page_index"] = objgen_to_page_index[ref]
    except Exception:
        for fig in figures:
            fig.setdefault("page_index", None)


def map_figures_to_images_by_page_and_mcid(
    structure:         Dict[str, Any],
    image_occurrences: List[Dict[str, Any]]
) -> None:
    figures = structure.get("figures", [])
    if not figures or not image_occurrences:
        return

    figures_by_page: Dict[int, List[Dict[str, Any]]] = {}
    images_by_page:  Dict[int, List[Dict[str, Any]]] = {}

    for fig in figures:
        page_index = fig.get("page_index")
        mcids      = fig.get("mcids", [])
        if page_index is None or not mcids:
            continue
        figures_by_page.setdefault(page_index, []).append(fig)

    for occ in image_occurrences:
        page_index = occ.get("page_index")
        if page_index is None:
            continue
        images_by_page.setdefault(page_index, []).append(occ)

    for page_index, page_images in images_by_page.items():
        page_figures = figures_by_page.get(page_index, [])
        if not page_figures:
            continue

        def occ_sort_key(occ):
            b = occ.get("bbox", [0, 0, 0, 0])
            return (round(b[1], 3), round(b[0], 3))

        def fig_sort_key(fig):
            mcids = fig.get("mcids", [])
            return mcids[0] if mcids else 10 ** 9

        page_images_sorted  = sorted(page_images,  key=occ_sort_key)
        page_figures_sorted = sorted(page_figures, key=fig_sort_key)

        if len(page_images_sorted) != len(page_figures_sorted):
            continue

        for occ, fig in zip(page_images_sorted, page_figures_sorted):
            alt         = fig.get("alt")
            actual_text = fig.get("actual_text")
            text_alt    = alt if alt is not None else actual_text

            occ["alt_text"]         = text_alt
            occ["alt_source"]       = "structure_page_mcid_match"
            occ["struct_figure_id"] = fig.get("id")
            occ["mapped_mcid"]      = fig.get("mcids", [None])[0]




