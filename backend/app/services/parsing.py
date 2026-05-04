import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from langdetect import detect, LangDetectException
from pdfminer.high_level import extract_pages
from pdfminer.layout import LTTextBoxHorizontal, LTTextLineHorizontal, LTChar, LTAnno

import fitz  # PyMuPDF
import pikepdf

from PIL import Image
import pytesseract
import io
import os
pytesseract.pytesseract.tesseract_cmd = os.environ.get(
    "TESSERACT_CMD",
    r"C:\Program Files\Tesseract-OCR\tesseract.exe"
)

import numpy as np

from .wcag.helper_function_b1 import (
    pdfminer_bbox_to_pymupdf_bbox, default_presentation_semantics, extract_widgets,
    annotate_text_in_image_context, annotate_logo_like_text, annotate_decorative_text,
    default_resize_risk, annotate_resize_risk, center_of_bbox, annotate_ui_labels,
    annotate_graphics_non_text_contrast, annotate_widgets_non_text_contrast,
    extract_media_occurrences, annotate_media_alternatives, contrast_ratio,
    extract_embedded_files_pikepdf, extract_media_annotations_pikepdf
)


# -----------------------------
# Helpers
# -----------------------------

def sha256_file(path: str) -> str:
    data = Path(path).read_bytes()
    return hashlib.sha256(data).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def int_color_to_rgb(color_int: Optional[int]) -> List[int]:
    if color_int is None or color_int < 0:
        return [0, 0, 0]
    r = (color_int >> 16) & 255
    g = (color_int >> 8) & 255
    b = color_int & 255
    return [r, g, b]


def float_rgb_to_int(rgb):
    if rgb is None:
        return None
    if isinstance(rgb, (list, tuple)) and len(rgb) >= 3:
        return [
            int(max(0, min(255, round(rgb[0] * 255)))),
            int(max(0, min(255, round(rgb[1] * 255)))),
            int(max(0, min(255, round(rgb[2] * 255))))
        ]
    return None


def center_of_bbox(b: List[float]) -> Tuple[float, float]:
    x0, y0, x1, y1 = b
    return ((x0 + x1) / 2.0, (y0 + y1) / 2.0)


def point_in_bbox(px: float, py: float, b: List[float], margin: float = 2.0) -> bool:
    x0, y0, x1, y1 = b
    return (x0 - margin) <= px <= (x1 + margin) and (y0 - margin) <= py <= (y1 + margin)


def pdfminer_bbox_to_pymupdf_bbox(bbox_pdfminer: List[float], page_height: float) -> List[float]:
    """
    pdfminer bbox: [x0, y0, x1, y1] with origin bottom-left.
    Convert to PyMuPDF-like: y grows downward (top-left style).
    """
    x0, y0, x1, y1 = bbox_pdfminer
    new_y0 = page_height - y1
    new_y1 = page_height - y0
    return [float(x0), float(new_y0), float(x1), float(new_y1)]


def srgb_channel_to_linear(c: float) -> float:
    c = c / 255.0
    if c <= 0.04045:
        return c / 12.92
    return ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(rgb: List[int]) -> float:
    r, g, b = rgb
    R = srgb_channel_to_linear(r)
    G = srgb_channel_to_linear(g)
    B = srgb_channel_to_linear(b)
    return 0.2126 * R + 0.7152 * G + 0.0722 * B


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


def is_bold_font(font: Dict[str, Any]) -> bool:
    name  = (font.get("name") or "").lower()
    flags = font.get("flags")
    if "bold" in name:
        return True
    if isinstance(flags, int) and (flags & 16):
        return True
    return False


def is_large_text(font: Dict[str, Any]) -> bool:
    """
    Returns True if the font qualifies as 'large text' per WCAG:
    18pt normal weight, or 14pt bold.
    Accepts a font dict with 'size' and optionally 'flags'/'name'.
    """
    size = float(font.get("size", 0.0))
    bold = is_bold_font(font)
    return size >= 18.0 or (bold and size >= 14.0)


def estimate_background_rgb_for_bbox(
    page: fitz.Page, bbox: List[float], scale: float = 2.0, grid: int = 8
) -> Optional[List[int]]:
    """
    Render page to an image and sample pixels under bbox.
    Returns median RGB from sampled pixels.
    """
    try:
        mat = fitz.Matrix(scale, scale)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        if pix.n < 3:
            return None
    except Exception:
        return None

    x0, y0, x1, y1 = bbox
    px0 = int(max(0, min(pix.width  - 1, round(x0 * scale))))
    py0 = int(max(0, min(pix.height - 1, round(y0 * scale))))
    px1 = int(max(0, min(pix.width  - 1, round(x1 * scale))))
    py1 = int(max(0, min(pix.height - 1, round(y1 * scale))))

    if px1 <= px0 or py1 <= py0:
        return None

    pad = max(1, int(min(px1 - px0, py1 - py0) * 0.12))
    sx0 = min(px1 - 1, px0 + pad)
    sy0 = min(py1 - 1, py0 + pad)
    sx1 = max(px0 + 1, px1 - pad)
    sy1 = max(py0 + 1, py1 - pad)

    if sx1 <= sx0 or sy1 <= sy0:
        sx0, sy0, sx1, sy1 = px0, py0, px1, py1

    xs = np.linspace(sx0, sx1 - 1, grid).astype(int)
    ys = np.linspace(sy0, sy1 - 1, grid).astype(int)

    samples = []
    for yy in ys:
        for xx in xs:
            rgb = img[yy, xx, :3]
            samples.append(rgb)

    if not samples:
        return None

    samples = np.array(samples)
    med = np.median(samples, axis=0)
    return [int(med[0]), int(med[1]), int(med[2])]


def compute_contrast_for_spans(
    doc: fitz.Document, text_spans: List[Dict[str, Any]], scale: float = 2.0
):
    """
    Adds span["background_estimate"] and span["contrast"] to each span.
    """
    spans_by_page: Dict[int, List[Dict[str, Any]]] = {}
    for s in text_spans:
        spans_by_page.setdefault(s["page_index"], []).append(s)

    for page_index, spans in spans_by_page.items():
        page = doc.load_page(page_index)

        for sp in spans:
            bbox = sp.get("bbox")
            if not bbox:
                continue

            fg   = sp.get("color", {}).get("fill_rgb", [0, 0, 0])
            font = sp.get("font", {})
            large = is_large_text(font)   # ← fixed: was is_large_text(font_size)

            bg = estimate_background_rgb_for_bbox(page, bbox, scale=scale, grid=8)
            if bg is None:
                bg     = [255, 255, 255]
                method = "fallback_white"
            else:
                method = "sampled_median"

            if not isinstance(fg, list) or len(fg) < 3:
                continue

            ratio = contrast_ratio(fg, bg)

            sp["background_estimate"] = {
                "bg_rgb":  bg,
                "method":  method,
                "samples": 64
            }
            sp["contrast"] = {
                "ratio":              float(round(ratio, 3)),
                "passes_4_5_1":       ratio >= 4.5,
                "passes_3_1_large":   ratio >= 3.0 if large else None,
                "large_text_assumed": large
            }


# -----------------------------
# Phase 1: PyMuPDF extractors
# -----------------------------

def extract_form_fields(page: fitz.Page, page_index: int):
    """
    Extract AcroForm fields via PyMuPDF widgets.
    Kept for batch2 compatibility.
    """
    fields = []
    field_counter = 0

    try:
        widgets = page.widgets()
    except Exception:
        widgets = []

    if not widgets:
        return fields

    for widget in widgets:
        try:
            rect = widget.rect
        except Exception:
            rect = None

        field_id = f"field_p{page_index}_{field_counter}"
        field_counter += 1

        fields.append({
            "id":         field_id,
            "page_index": page_index,
            "name":       getattr(widget, "field_name",  None),
            "field_type": str(getattr(widget, "field_type", None)),
            "label":      None,
            "value":      getattr(widget, "field_value", None),
            "bbox": [
                float(rect.x0), float(rect.y0),
                float(rect.x1), float(rect.y1)
            ] if rect else None
        })

    return fields


def extract_images(page: fitz.Page, doc: fitz.Document, page_index: int):
    image_assets    = {}
    image_occurrences = []
    asset_bytes_map = {}

    images      = page.get_images(full=True)
    occ_counter = 0

    for img in images:
        xref = img[0]

        try:
            base_image = doc.extract_image(xref)
            img_bytes  = base_image.get("image", b"")
        except Exception:
            continue

        if not img_bytes:
            continue

        img_hash = sha256_bytes(img_bytes)
        asset_id = f"img_asset_{img_hash}"

        if asset_id not in image_assets:
            image_assets[asset_id] = {
                "asset_id": asset_id,
                "width":    base_image.get("width"),
                "height":   base_image.get("height"),
                "format":   base_image.get("ext"),
                "hash":     img_hash
            }
            asset_bytes_map[asset_id] = img_bytes

        rects = page.get_image_rects(xref)
        for r in rects:
            occ_id = f"img_occ_p{page_index}_{occ_counter}"
            occ_counter += 1

            image_occurrences.append({
                "id":               occ_id,
                "asset_id":         asset_id,
                "page_index":       page_index,
                "bbox":             [float(r.x0), float(r.y0), float(r.x1), float(r.y1)],
                "alt_text":         None,
                "alt_source":       None,
                "struct_figure_id": None,
                "ocr_text":         None,
                "ocr_confidence":   None
            })

    return image_assets, image_occurrences, asset_bytes_map


def extract_links(page: fitz.Page, page_index: int):
    """
    Extract links in a rule-friendly format.
    Keeps 'kind' for backward compatibility and also adds 'type' + 'uri'.
    """
    links        = []
    link_counter = 0

    for l in page.get_links():
        rect = l.get("from")
        if rect is None:
            continue

        link_id = f"link_p{page_index}_{link_counter}"
        link_counter += 1

        link_type = "unknown"
        target    = None
        uri       = None

        if "uri" in l and l.get("uri"):
            link_type = "uri"
            uri       = l.get("uri")
            target    = uri
        elif "page" in l and l.get("page") is not None:
            link_type = "internal"
            target    = f"page_{l.get('page')}"
        elif "to" in l and l.get("to") is not None:
            link_type = "internal"
            target    = str(l.get("to"))

        links.append({
            "id":         link_id,
            "page_index": page_index,
            "bbox":       [float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)],
            "kind":       link_type,
            "type":       link_type,
            "target":     target,
            "uri":        uri
        })

    return links


def extract_graphics(page: fitz.Page, page_index: int):
    graphics  = []
    g_counter = 0

    try:
        drawings = page.get_drawings()
    except Exception:
        drawings = []

    for d in drawings:
        rect = d.get("rect")
        if rect is None:
            continue

        graphic_id = f"gfx_p{page_index}_{g_counter}"
        g_counter += 1

        graphics.append({
            "id":           graphic_id,
            "page_index":   page_index,
            "bbox":         [float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)],
            "stroke_rgb":   float_rgb_to_int(d.get("color")),
            "fill_rgb":     float_rgb_to_int(d.get("fill")),
            "stroke_width": float(d.get("width")) if d.get("width") is not None else None,
            "opacity":      float(d.get("opacity")) if d.get("opacity") is not None else None,
            "type":         d.get("type", "path")
        })

    return graphics


def extract_bookmarks(doc: fitz.Document):
    """
    Extract document outline / bookmarks using PyMuPDF.
    Kept for batch2 compatibility.
    """
    bookmarks = []

    try:
        toc = doc.get_toc(simple=False)
    except Exception:
        toc = []

    if not toc:
        return bookmarks

    for i, item in enumerate(toc):
        try:
            level = item[0] if len(item) > 0 else None
            title = item[1] if len(item) > 1 else None
            page  = item[2] if len(item) > 2 else None
            dest  = item[3] if len(item) > 3 else None

            bookmarks.append({
                "id":          f"bookmark_{i}",
                "level":       int(level) if level is not None else None,
                "title":       str(title).strip() if title else None,
                "page_index":  int(page - 1) if isinstance(page, int) and page > 0 else None,
                "destination": str(dest) if dest is not None else None
            })
        except Exception:
            continue

    return bookmarks


# -----------------------------
# Phase 2: pdfminer blocks + reading order
# -----------------------------

def extract_pdfminer_blocks(pdf_path: str):
    text_blocks   = []
    reading_order = []
    block_counter = 0

    for page_index, layout in enumerate(extract_pages(pdf_path)):
        lines = []

        for element in layout:
            if isinstance(element, LTTextBoxHorizontal):
                for line in element:
                    if isinstance(line, LTTextLineHorizontal):
                        chars = []
                        for obj in line:
                            if isinstance(obj, LTChar):
                                chars.append(obj.get_text())
                            elif isinstance(obj, LTAnno):
                                chars.append(obj.get_text())

                        line_text = "".join(chars).strip()
                        if not line_text:
                            continue

                        x0, y0, x1, y1 = line.bbox
                        lines.append((y1, x0, x1, y0, line_text))

        lines.sort(key=lambda t: (-t[0], t[1]))
        current = []
        last_y  = None

        def flush_block():
            nonlocal block_counter, current
            if not current:
                return

            block_id = f"block_p{page_index}_{block_counter}"
            block_counter += 1

            x0s  = [t[1] for t in current]
            x1s  = [t[2] for t in current]
            y0s  = [t[3] for t in current]
            y1s  = [t[0] for t in current]
            bbox = [min(x0s), min(y0s), max(x1s), max(y1s)]
            text = "\n".join([t[4] for t in current])

            text_blocks.append({
                "id":           block_id,
                "page_index":   page_index,
                "bbox_pdfminer": [float(bbox[0]), float(bbox[1]),
                                  float(bbox[2]), float(bbox[3])],
                "text":         text,
                "span_ids":     []
            })

            reading_order.append(block_id)
            current = []

        for (y_top, x0, x1, y_bottom, text) in lines:
            if last_y is None:
                current.append((y_top, x0, x1, y_bottom, text))
                last_y = y_top
                continue

            if abs(last_y - y_top) > 30:
                flush_block()

            current.append((y_top, x0, x1, y_bottom, text))
            last_y = y_top

        flush_block()

    return text_blocks, reading_order


# -----------------------------
# Phase 2.5: Align blocks -> spans
# -----------------------------

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


# -----------------------------
# Structure extraction (pikepdf)
# -----------------------------

def _pike_to_py(obj):
    try:
        if isinstance(obj, pikepdf.Name):
            return str(obj)[1:]
        if isinstance(obj, (pikepdf.String, str)):
            return str(obj)
        if isinstance(obj, (int, float, bool)):
            return obj
        if obj is None:
            return None
        if isinstance(obj, pikepdf.Array):
            return [_pike_to_py(x) for x in obj]
        if isinstance(obj, pikepdf.Dictionary):
            out = {}
            for k, v in obj.items():
                out[str(k)[1:] if isinstance(k, pikepdf.Name) else str(k)] = _pike_to_py(v)
            return out
        return str(obj)
    except Exception:
        return str(obj)


def _flatten_structure_nodes(tree: Any) -> List[Dict[str, Any]]:
    nodes: List[Dict[str, Any]] = []

    def walk(node):
        if not isinstance(node, dict):
            return
        nodes.append(node)
        for child in node.get("children", []):
            if isinstance(child, dict):
                walk(child)

    if isinstance(tree, list):
        for root in tree:
            walk(root)

    return nodes


def extract_figure_nodes_from_structure(structure: Dict[str, Any]) -> List[Dict[str, Any]]:
    figures:    List[Dict[str, Any]] = []
    tree        = structure.get("tree")
    flat_nodes  = _flatten_structure_nodes(tree)

    for node in flat_nodes:
        if node.get("role") != "Figure":
            continue

        alt         = node.get("alt")
        actual_text = node.get("actual_text")

        figures.append({
            "id":           node.get("id"),
            "role":         "Figure",
            "alt":          alt,
            "actual_text":  actual_text,
            "is_decorative": alt is not None and str(alt).strip() == "",
            "depth":        node.get("depth"),
            "mcids":        node.get("mcids", []),
            "page_object_ref": node.get("page_object_ref"),
            "children":     node.get("children", []),
        })

    return figures


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


def extract_structure_pikepdf(pdf_path: str):
    """
    Extract /Lang, /StructTreeRoot presence, /RoleMap, and simplified structure tree.
    """
    result = {
        "has_tags":   False,
        "lang":       None,
        "role_map":   None,
        "tree":       None,
        "validation": {"errors": [], "notes": []}
    }

    try:
        with pikepdf.open(pdf_path) as pdf:
            root = pdf.Root

            # /Lang — with parentheses stripping for pikepdf artifact
            try:
                if "/Lang" in root:
                    lang_val = str(root["/Lang"]).strip()
                    if lang_val.startswith("(") and lang_val.endswith(")"):
                        lang_val = lang_val[1:-1].strip()
                    result["lang"] = lang_val or None
            except Exception:
                result["validation"]["notes"].append("Could not read /Lang")

            if "/StructTreeRoot" not in root:
                result["validation"]["errors"].append(
                    "No /StructTreeRoot (PDF is likely untagged)"
                )
                return result

            result["has_tags"]  = True
            struct_root = root["/StructTreeRoot"]

            try:
                if "/RoleMap" in struct_root:
                    result["role_map"] = _pike_to_py(struct_root["/RoleMap"])
            except Exception:
                result["validation"]["notes"].append("Could not read /RoleMap")

            if "/K" not in struct_root:
                result["validation"]["errors"].append("StructTreeRoot has no /K children")
                result["tree"] = []
                return result

            kids = struct_root["/K"]
            if not isinstance(kids, pikepdf.Array):
                kids = pikepdf.Array([kids])

            nodes        = []
            node_counter = 0

            def walk(node, depth=0):
                nonlocal node_counter
                node_id = f"node_{node_counter}"
                node_counter += 1

                role = None
                try:
                    if isinstance(node, pikepdf.Dictionary) and "/S" in node:
                        role = str(node["/S"])[1:]
                except Exception:
                    role = None

                out_node = {
                    "id":              node_id,
                    "role":            role,
                    "depth":           depth,
                    "children":        [],
                    "alt":             None,
                    "actual_text":     None,
                    "mcids":           [],
                    "page_object_ref": None,
                }

                try:
                    if isinstance(node, pikepdf.Dictionary) and "/Alt" in node:
                        out_node["alt"] = str(node["/Alt"])
                except Exception:
                    pass

                try:
                    if isinstance(node, pikepdf.Dictionary) and "/ActualText" in node:
                        out_node["actual_text"] = str(node["/ActualText"])
                except Exception:
                    pass

                try:
                    if isinstance(node, pikepdf.Dictionary) and "/Pg" in node:
                        pg = node["/Pg"]
                        if hasattr(pg, "objgen"):
                            out_node["page_object_ref"] = pg.objgen
                except Exception:
                    pass

                try:
                    if isinstance(node, pikepdf.Dictionary) and "/K" in node:
                        child = node["/K"]

                        def handle_k_item(item):
                            if isinstance(item, int):
                                out_node["mcids"].append(item)
                                out_node["children"].append({"type": "mcid", "value": int(item)})
                                return

                            if isinstance(item, pikepdf.Dictionary):
                                if "/MCID" in item:
                                    try:
                                        mcid_val = int(item["/MCID"])
                                        out_node["mcids"].append(mcid_val)
                                    except Exception:
                                        mcid_val = None

                                    pg_ref = None
                                    try:
                                        if "/Pg" in item and hasattr(item["/Pg"], "objgen"):
                                            pg_ref = item["/Pg"].objgen
                                    except Exception:
                                        pass

                                    out_node["children"].append({
                                        "type":            "mcr",
                                        "mcid":            mcid_val,
                                        "page_object_ref": pg_ref,
                                    })
                                    return

                                out_node["children"].append(walk(item, depth + 1))
                                return

                            out_node["children"].append({
                                "type":  "leaf",
                                "value": _pike_to_py(item)
                            })

                        if isinstance(child, pikepdf.Array):
                            for c in child:
                                handle_k_item(c)
                        else:
                            handle_k_item(child)

                except Exception:
                    result["validation"]["notes"].append(
                        f"Failed walking children at depth {depth}"
                    )

                return out_node

            for k in kids:
                if isinstance(k, pikepdf.Dictionary):
                    nodes.append(walk(k, 0))
                else:
                    nodes.append({"type": "leaf", "value": _pike_to_py(k)})

            result["tree"] = nodes
            return result

    except Exception as e:
        result["validation"]["errors"].append(f"pikepdf failed: {type(e).__name__}: {e}")
        return result


# -----------------------------
# OCR
# -----------------------------

def ocr_image_bytes(img_bytes: bytes, lang: str = "eng"):
    """Returns (text, confidence)."""
    try:
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    except Exception:
        return None, None

    try:
        text = pytesseract.image_to_string(img, lang=lang).strip()
    except Exception:
        return None, None

    conf = None
    try:
        data  = pytesseract.image_to_data(img, lang=lang, output_type=pytesseract.Output.DICT)
        confs = []
        for c in data.get("conf", []):
            try:
                c = float(c)
                if c >= 0:
                    confs.append(c)
            except Exception:
                pass
        if confs:
            conf = sum(confs) / len(confs)
    except Exception:
        conf = None

    return text if text else None, conf


def run_ocr_on_image_occurrences(image_occurrences, asset_bytes_global, min_px: int = 40_000):
    for occ in image_occurrences:
        asset_id = occ["asset_id"]
        b        = asset_bytes_global.get(asset_id)
        if not b:
            continue

        try:
            img  = Image.open(io.BytesIO(b))
            w, h = img.size
            if (w * h) < min_px:
                continue
        except Exception:
            pass

        text, conf           = ocr_image_bytes(b, lang="eng")
        occ["ocr_text"]      = text
        occ["ocr_confidence"] = conf


# -----------------------------
# Interactivity extraction (pikepdf)
# -----------------------------

def extract_interactivity_pikepdf(pdf_path: str) -> Dict[str, Any]:
    """
    Extract interactivity signals needed for WCAG 2.1.x / 3.3.x / 4.1.2 checks.
    """
    result: Dict[str, Any] = {
        "has_javascript":      False,
        "javascript_triggers": [],
        "has_acroform":        False,
        "acroform_fields":     [],
        "tab_order":           [],
        "has_tab_order":       False,
    }

    try:
        with pikepdf.open(pdf_path) as pdf:
            root = pdf.Root

            # ── JavaScript detection ─────────────────────────────────────────
            try:
                if "/OpenAction" in root:
                    action = root["/OpenAction"]
                    if isinstance(action, pikepdf.Dictionary):
                        s = action.get("/S")
                        if s and str(s) in ("/JavaScript", "/JS"):
                            result["has_javascript"] = True
                            result["javascript_triggers"].append({
                                "trigger": "OpenAction", "location": "document"
                            })
            except Exception:
                pass

            try:
                if "/AA" in root:
                    aa = root["/AA"]
                    if isinstance(aa, pikepdf.Dictionary):
                        for key in aa.keys():
                            entry = aa[key]
                            if isinstance(entry, pikepdf.Dictionary):
                                s = entry.get("/S")
                                if s and str(s) in ("/JavaScript", "/JS"):
                                    result["has_javascript"] = True
                                    result["javascript_triggers"].append({
                                        "trigger":  f"DocumentAA/{str(key)[1:]}",
                                        "location": "document"
                                    })
            except Exception:
                pass

            try:
                if "/Names" in root:
                    names = root["/Names"]
                    if isinstance(names, pikepdf.Dictionary) and "/JavaScript" in names:
                        result["has_javascript"] = True
                        result["javascript_triggers"].append({
                            "trigger": "Names/JavaScript", "location": "document"
                        })
            except Exception:
                pass

            try:
                for page_index, page in enumerate(pdf.pages):
                    if "/AA" in page:
                        aa = page["/AA"]
                        if isinstance(aa, pikepdf.Dictionary):
                            for key in aa.keys():
                                entry = aa[key]
                                if isinstance(entry, pikepdf.Dictionary):
                                    s = entry.get("/S")
                                    if s and str(s) in ("/JavaScript", "/JS"):
                                        result["has_javascript"] = True
                                        result["javascript_triggers"].append({
                                            "trigger":  f"PageAA/{str(key)[1:]}",
                                            "location": f"page_{page_index}"
                                        })
            except Exception:
                pass

            # ── Tab order ────────────────────────────────────────────────────
            try:
                for page_index, page in enumerate(pdf.pages):
                    tabs_val = None
                    if "/Tabs" in page:
                        tabs_val = str(page["/Tabs"])[1:]
                        result["has_tab_order"] = True
                    result["tab_order"].append({
                        "page_index": page_index,
                        "tabs":       tabs_val
                    })
            except Exception:
                pass

            # ── Submit actions ───────────────────────────────────────────────
            result["submit_actions"] = []
            try:
                if "/AcroForm" in root:
                    acroform   = root["/AcroForm"]
                    if isinstance(acroform, pikepdf.Dictionary):
                        fields_arr = acroform.get("/Fields")
                        if fields_arr and isinstance(fields_arr, pikepdf.Array):

                            def find_submit(field_obj, counter=[0]):
                                if not isinstance(field_obj, pikepdf.Dictionary):
                                    return
                                try:
                                    if "/A" in field_obj:
                                        action = field_obj["/A"]
                                        if isinstance(action, pikepdf.Dictionary):
                                            s = action.get("/S")
                                            if s and str(s) == "/SubmitForm":
                                                url = None
                                                try:
                                                    f_entry = action.get("/F")
                                                    if f_entry and isinstance(f_entry, pikepdf.Dictionary):
                                                        url = str(f_entry.get("/FS") or f_entry.get("/F") or "")
                                                except Exception:
                                                    pass
                                                result["submit_actions"].append({
                                                    "field_id": f"field_{counter[0]}",
                                                    "action":   "SubmitForm",
                                                    "url":      url,
                                                })
                                except Exception:
                                    pass
                                try:
                                    if "/Kids" in field_obj:
                                        kids = field_obj["/Kids"]
                                        if isinstance(kids, pikepdf.Array):
                                            for kid in kids:
                                                try:
                                                    obj = kid if isinstance(kid, pikepdf.Dictionary) else kid.get_object()
                                                    counter[0] += 1
                                                    find_submit(obj, counter)
                                                except Exception:
                                                    pass
                                except Exception:
                                    pass

                            for field_ref in fields_arr:
                                try:
                                    obj = field_ref if isinstance(field_ref, pikepdf.Dictionary) else field_ref.get_object()
                                    find_submit(obj)
                                except Exception:
                                    pass
            except Exception:
                pass

            result["has_submit_action"] = len(result["submit_actions"]) > 0

            # ── AcroForm fields ──────────────────────────────────────────────
            try:
                if "/AcroForm" not in root:
                    return result

                acroform = root["/AcroForm"]
                if not isinstance(acroform, pikepdf.Dictionary):
                    return result

                result["has_acroform"] = True

                fields_array = acroform.get("/Fields")
                if not fields_array or not isinstance(fields_array, pikepdf.Array):
                    return result

                field_counter = 0

                def process_field(field_obj: pikepdf.Dictionary, page_index):
                    nonlocal field_counter

                    if "/Kids" in field_obj:
                        kids = field_obj["/Kids"]
                        if isinstance(kids, pikepdf.Array):
                            for kid in kids:
                                try:
                                    obj = kid if isinstance(kid, pikepdf.Dictionary) else kid.get_object()
                                    process_field(obj, page_index)
                                except Exception:
                                    pass
                        return

                    field_id = f"field_{field_counter}"
                    field_counter += 1

                    ft = None
                    try:
                        if "/FT" in field_obj:
                            ft = str(field_obj["/FT"])[1:]
                    except Exception:
                        pass

                    name = None
                    try:
                        if "/T" in field_obj:
                            name = str(field_obj["/T"])
                    except Exception:
                        pass

                    tooltip = None
                    try:
                        if "/TU" in field_obj:
                            tooltip = str(field_obj["/TU"])
                    except Exception:
                        pass

                    ff = 0
                    try:
                        if "/Ff" in field_obj:
                            ff = int(field_obj["/Ff"])
                    except Exception:
                        pass

                    read_only = bool(ff & 1)
                    required  = bool(ff & 2)

                    pg_index = page_index
                    try:
                        if "/P" in field_obj:
                            p_ref = field_obj["/P"]
                            for i, pg in enumerate(pdf.pages):
                                if pg.objgen == p_ref.objgen:
                                    pg_index = i
                                    break
                    except Exception:
                        pass

                    field_aa = {}
                    try:
                        if "/AA" in field_obj:
                            aa = field_obj["/AA"]
                            if isinstance(aa, pikepdf.Dictionary):
                                for aa_key in aa.keys():
                                    entry = aa[aa_key]
                                    is_js = False
                                    if isinstance(entry, pikepdf.Dictionary):
                                        s = entry.get("/S")
                                        if s and str(s) in ("/JavaScript", "/JS"):
                                            is_js = True
                                    field_aa[str(aa_key)[1:]] = {"has_javascript": is_js}
                    except Exception:
                        pass

                    appearance_state = None
                    try:
                        if "/AS" in field_obj:
                            appearance_state = str(field_obj["/AS"])[1:]
                    except Exception:
                        pass

                    result["acroform_fields"].append({
                        "id":                 field_id,
                        "name":               name,
                        "type":               ft,
                        "flags":              ff,
                        "read_only":          read_only,
                        "required":           required,
                        "tooltip":            tooltip,
                        "page_index":         pg_index,
                        "validation_actions": field_aa,
                        "appearance_state":   appearance_state,
                    })

                for field_ref in fields_array:
                    try:
                        obj = field_ref if isinstance(field_ref, pikepdf.Dictionary) else field_ref.get_object()
                        process_field(obj, None)
                    except Exception:
                        pass

            except Exception:
                pass

    except Exception as e:
        result["error"] = f"pikepdf failed: {type(e).__name__}: {e}"

    return result


# -----------------------------
# Main extraction
# -----------------------------

def extract_document_json(pdf_path: str, run_ocr: bool = True) -> Dict[str, Any]:
    # ── Phase 2: pdfminer blocks + reading order ──────────────────────────────
    text_blocks, reading_order = extract_pdfminer_blocks(pdf_path)

    # ── Phase 1: PyMuPDF visual extraction ───────────────────────────────────
    doc = fitz.open(pdf_path)

    # Metadata (restored: title, author, subject, keywords)
    pdf_meta = doc.metadata or {}
    title    = pdf_meta.get("title")
    author   = pdf_meta.get("author")
    subject  = pdf_meta.get("subject")
    keywords = pdf_meta.get("keywords")

    pages:                 List[Dict[str, Any]] = []
    text_spans:            List[Dict[str, Any]] = []
    all_image_assets:      Dict[str, Any]       = {}
    all_image_occurrences: List[Dict[str, Any]] = []
    all_links:             List[Dict[str, Any]] = []
    all_graphics:          List[Dict[str, Any]] = []
    all_asset_bytes:       Dict[str, bytes]     = {}
    all_widgets:           List[Dict[str, Any]] = []
    all_form_fields:       List[Dict[str, Any]] = []  # restored for batch2

    for page_index in range(doc.page_count):
        page = doc.load_page(page_index)

        pages.append({
            "page_index": page_index,
            "width":      float(page.rect.width),
            "height":     float(page.rect.height),
            "rotation":   int(page.rotation)
        })

        all_widgets.extend(extract_widgets(page, page_index))
        all_form_fields.extend(extract_form_fields(page, page_index))  # restored

        # Text spans
        text_dict    = page.get_text("dict")
        span_counter = 0

        for block_idx, block in enumerate(text_dict.get("blocks", [])):
            if block.get("type") != 0:
                continue
            for line_idx, line in enumerate(block.get("lines", [])):
                for span in line.get("spans", []):
                    bbox = span.get("bbox")
                    txt  = span.get("text", "")
                    if not bbox or not txt.strip():
                        continue

                    detected_lang = detect_language_safe(txt)  # restored for batch2

                    span_id = f"span_p{page_index}_s{span_counter}"
                    span_counter += 1

                    text_spans.append({
                        "id":                span_id,
                        "page_index":        page_index,
                        "bbox":              [float(bbox[0]), float(bbox[1]),
                                              float(bbox[2]), float(bbox[3])],
                        "text":              txt,
                        "detected_language": detected_lang,        # restored for batch2
                        "font": {
                            "name":  span.get("font", ""),
                            "size":  float(span.get("size", 0.0)),
                            "flags": span.get("flags", None)
                        },
                        "color": {
                            "fill_rgb": int_color_to_rgb(span.get("color"))
                        },
                        "layout": {                                # incoming v2
                            "block_index": block_idx,
                            "line_index":  line_idx
                        },
                        "presentation_semantics": default_presentation_semantics(),  # v2
                        "resize_risk":            default_resize_risk(),              # v2
                    })

        assets, occurrences, asset_bytes_map = extract_images(page, doc, page_index)
        for aid, asset in assets.items():
            all_image_assets[aid] = asset
        for aid, img_bytes in asset_bytes_map.items():
            all_asset_bytes[aid] = img_bytes
        all_image_occurrences.extend(occurrences)

        all_links.extend(extract_links(page, page_index))
        all_graphics.extend(extract_graphics(page, page_index))

    # ── Phase 5: contrast + media (needs open doc) ───────────────────────────
    compute_contrast_for_spans(doc, text_spans, scale=2.0)
    annotate_graphics_non_text_contrast(doc, all_graphics, scale=2.0)
    annotate_widgets_non_text_contrast(doc, all_widgets, scale=2.0)

    media_occurrences = []
    media_occurrences.extend(extract_media_occurrences(doc, text_spans))
    media_occurrences.extend(extract_media_annotations_pikepdf(pdf_path, pages))
    media_occurrences.extend(extract_embedded_files_pikepdf(pdf_path))
    annotate_media_alternatives(media_occurrences, text_spans)

    # Bookmarks extracted before doc.close()
    bookmarks = extract_bookmarks(doc)  # restored for batch2

    doc.close()

    # ── Phase 4: OCR ──────────────────────────────────────────────────────────
    if run_ocr:
        run_ocr_on_image_occurrences(all_image_occurrences, all_asset_bytes)

    # ── Annotations ───────────────────────────────────────────────────────────
    annotate_text_in_image_context(text_spans, all_image_occurrences)
    annotate_logo_like_text(text_spans, pages)
    annotate_decorative_text(text_spans)
    annotate_ui_labels(text_spans, all_widgets)
    annotate_resize_risk(text_spans, all_graphics, all_widgets, pages)

    # ── Phase 2.5: align blocks → spans ──────────────────────────────────────
    text_blocks = align_blocks_to_spans(text_blocks, text_spans, pages)

    # ── Derived fields (restored for batch2) ─────────────────────────────────
    inferred_language  = infer_document_language(text_spans)
    heading_candidates = detect_heading_candidates(text_spans)

    # ── Structure + figure mapping ────────────────────────────────────────────
    file_hash = sha256_file(pdf_path)
    structure = extract_structure_pikepdf(pdf_path)

    structure["figures"] = extract_figure_nodes_from_structure(structure)
    attach_page_index_to_figures(structure["figures"], pdf_path)
    map_single_figure_alt_to_single_image(structure, all_image_occurrences)
    map_figures_to_images_by_page_and_mcid(structure, all_image_occurrences)
    map_figures_to_images_by_order(structure, all_image_occurrences)

    # ── Interactivity (required by batch3) ───────────────────────────────────
    interactivity = extract_interactivity_pikepdf(pdf_path)

    # ── Assemble output ───────────────────────────────────────────────────────
    out = {
        "document": {
            "metadata": {
                "filename":          Path(pdf_path).name,
                "title":             title,     # restored for batch2
                "author":            author,    # restored for out3.json
                "subject":           subject,   # restored for out3.json
                "keywords":          keywords,  # restored for out3.json
                "file_hash_sha256":  file_hash,
                "page_count":        len(pages),
                "coordinate_system": {
                    "units": "pt",
                    "note":  (
                        "PyMuPDF bboxes stored in document.*.bbox; "
                        "pdfminer raw bboxes stored in text_blocks[].bbox_pdfminer"
                    )
                }
            },
            "pages":              pages,
            "text_spans":         text_spans,
            "text_blocks":        text_blocks,
            "images": {
                "assets":      list(all_image_assets.values()),
                "occurrences": all_image_occurrences
            },
            "graphics":           all_graphics,
            "links":              all_links,
            "bookmarks":          bookmarks,        # restored for batch2
            "form_fields":        all_form_fields,  # restored for batch2
            "widgets":            all_widgets,
            "media": {
                "occurrences": media_occurrences
            },
            "structure":          structure,
            "interactivity":      interactivity,    # required by batch3
            "inferred_language":  inferred_language,    # restored for batch2
            "heading_candidates": heading_candidates,   # restored for batch2
            "reading_order": {
                "source": "pdfminer",
                "order":  reading_order,
                "note":   "reading order is block IDs"
            }
        }
    }

    return out


