import json
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
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

import numpy as np


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
        if not text or len(text.strip()) < 5:
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

        is_large = size >= max(16, avg_font_size * 1.2)
        is_boldish = any(word in font_name for word in ["bold", "black", "semibold", "demi"])

        if is_large or (is_boldish and size >= avg_font_size):
            candidates.append(span["id"])

    return candidates

def contrast_ratio(fg_rgb: List[int], bg_rgb: List[int]) -> float:
    L1 = relative_luminance(fg_rgb)
    L2 = relative_luminance(bg_rgb)
    lighter = max(L1, L2)
    darker = min(L1, L2)
    return (lighter + 0.05) / (darker + 0.05)


def is_large_text(font_size_pt: float) -> bool:
    return font_size_pt >= 18.0


def estimate_background_rgb_for_bbox(page: fitz.Page, bbox: List[float], scale: float = 2.0, grid: int = 8) -> Optional[List[int]]:
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
    px0 = int(max(0, min(pix.width - 1, round(x0 * scale))))
    py0 = int(max(0, min(pix.height - 1, round(y0 * scale))))
    px1 = int(max(0, min(pix.width - 1, round(x1 * scale))))
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


def compute_contrast_for_spans(doc: fitz.Document, text_spans: List[Dict[str, Any]], scale: float = 2.0):
    """
    Adds:
      span["background_estimate"]
      span["contrast"]
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

            fg = sp.get("color", {}).get("fill_rgb", [0, 0, 0])
            font_size = float(sp.get("font", {}).get("size", 0.0))

            bg = estimate_background_rgb_for_bbox(page, bbox, scale=scale, grid=8)
            if bg is None:
                bg = [255, 255, 255]
                method = "fallback_white"
            else:
                method = "sampled_median"

            ratio = contrast_ratio(fg, bg)
            large = is_large_text(font_size)

            sp["background_estimate"] = {
                "bg_rgb": bg,
                "method": method,
                "samples": 64
            }
            sp["contrast"] = {
                "ratio": float(round(ratio, 3)),
                "passes_4_5_1": ratio >= 4.5,
                "passes_3_1_large": ratio >= 3.0 if large else None,
                "large_text_assumed": large
            }


# -----------------------------
# Phase 1: PyMuPDF extractors
# -----------------------------

def extract_form_fields(page: fitz.Page, page_index: int):
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
            "id": field_id,
            "page_index": page_index,
            "name": getattr(widget, "field_name", None),
            "field_type": str(getattr(widget, "field_type", None)),
            "label": None,
            "value": getattr(widget, "field_value", None),
            "bbox": [
                float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)
            ] if rect else None
        })

    return fields


def extract_images(page: fitz.Page, doc: fitz.Document, page_index: int):
    image_assets = {}
    image_occurrences = []
    asset_bytes_map = {}

    images = page.get_images(full=True)
    occ_counter = 0

    for img in images:
        xref = img[0]

        try:
            base_image = doc.extract_image(xref)
            img_bytes = base_image.get("image", b"")
        except Exception:
            continue

        if not img_bytes:
            continue

        img_hash = sha256_bytes(img_bytes)
        asset_id = f"img_asset_{img_hash}"

        if asset_id not in image_assets:
            image_assets[asset_id] = {
                "asset_id": asset_id,
                "width": base_image.get("width"),
                "height": base_image.get("height"),
                "format": base_image.get("ext"),
                "hash": img_hash
            }
            asset_bytes_map[asset_id] = img_bytes

        rects = page.get_image_rects(xref)
        for r in rects:
            occ_id = f"img_occ_p{page_index}_{occ_counter}"
            occ_counter += 1

            image_occurrences.append({
                "id": occ_id,
                "asset_id": asset_id,
                "page_index": page_index,
                "bbox": [float(r.x0), float(r.y0), float(r.x1), float(r.y1)],
                "alt_text": None,
                "ocr_text": None,
                "ocr_confidence": None
            })

    return image_assets, image_occurrences, asset_bytes_map


def extract_links(page: fitz.Page, page_index: int):
    """
    Extract links in a rule-friendly format.
    Keeps 'kind' for backward compatibility and also adds 'type' + 'uri'.
    """
    links = []
    link_counter = 0

    for l in page.get_links():
        rect = l.get("from")
        if rect is None:
            continue

        link_id = f"link_p{page_index}_{link_counter}"
        link_counter += 1

        link_type = "unknown"
        target = None
        uri = None

        if "uri" in l and l.get("uri"):
            link_type = "uri"
            uri = l.get("uri")
            target = uri
        elif "page" in l and l.get("page") is not None:
            link_type = "internal"
            target = f"page_{l.get('page')}"
        elif "to" in l and l.get("to") is not None:
            link_type = "internal"
            target = str(l.get("to"))

        links.append({
            "id": link_id,
            "page_index": page_index,
            "bbox": [float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)],
            "kind": link_type,
            "type": link_type,
            "target": target,
            "uri": uri
        })

    return links


def extract_graphics(page: fitz.Page, page_index: int):
    graphics = []
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
            "id": graphic_id,
            "page_index": page_index,
            "bbox": [float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)],
            "stroke_rgb": float_rgb_to_int(d.get("color")),
            "fill_rgb": float_rgb_to_int(d.get("fill")),
            "stroke_width": float(d.get("width")) if d.get("width") is not None else None,
            "opacity": float(d.get("opacity")) if d.get("opacity") is not None else None,
            "type": d.get("type", "path")
        })

    return graphics


def extract_bookmarks(doc: fitz.Document):
    """
    Extract document outline / bookmarks using PyMuPDF.
    Returns a flat list of bookmark entries.
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
            page = item[2] if len(item) > 2 else None
            dest = item[3] if len(item) > 3 else None

            bookmarks.append({
                "id": f"bookmark_{i}",
                "level": int(level) if level is not None else None,
                "title": str(title).strip() if title else None,
                "page_index": int(page - 1) if isinstance(page, int) and page > 0 else None,
                "destination": str(dest) if dest is not None else None
            })
        except Exception:
            continue

    return bookmarks


# -----------------------------
# Phase 2: pdfminer blocks + reading order
# -----------------------------

def extract_pdfminer_blocks(pdf_path: str):
    text_blocks = []
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
        last_y = None

        def flush_block():
            nonlocal block_counter, current
            if not current:
                return

            block_id = f"block_p{page_index}_{block_counter}"
            block_counter += 1

            x0s = [t[1] for t in current]
            x1s = [t[2] for t in current]
            y0s = [t[3] for t in current]
            y1s = [t[0] for t in current]

            bbox = [min(x0s), min(y0s), max(x1s), max(y1s)]
            text = "\n".join([t[4] for t in current])

            text_blocks.append({
                "id": block_id,
                "page_index": page_index,
                "bbox_pdfminer": [float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])],
                "text": text,
                "span_ids": []
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
    text_spans: List[Dict[str, Any]],
    pages: List[Dict[str, Any]],
):
    """
    Fill each text_blocks[i]["span_ids"] by matching span centers into block bbox.
    Also adds "bbox" for the block in PyMuPDF coordinates.
    """
    spans_by_page: Dict[int, List[Dict[str, Any]]] = {}
    for s in text_spans:
        spans_by_page.setdefault(s["page_index"], []).append(s)

    page_height: Dict[int, float] = {p["page_index"]: float(p["height"]) for p in pages}

    for blk in text_blocks:
        pno = blk["page_index"]
        h = page_height.get(pno)
        if h is None:
            continue

        bbox_pym = pdfminer_bbox_to_pymupdf_bbox(blk["bbox_pdfminer"], h)
        blk["bbox"] = bbox_pym

        candidates = spans_by_page.get(pno, [])
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
# Structure extraction with pikepdf
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


def extract_structure_pikepdf(pdf_path: str):
    """
    Extract:
      - /Lang
      - /StructTreeRoot presence
      - /RoleMap
      - simplified structure tree
    """
    result = {
        "has_tags": False,
        "lang": None,
        "role_map": None,
        "tree": None,
        "validation": {
            "errors": [],
            "notes": []
        }
    }

    try:
        with pikepdf.open(pdf_path) as pdf:
            root = pdf.Root

            try:
                if "/Lang" in root:
                    lang_val = str(root["/Lang"]).strip()
                    if lang_val.startswith("(") and lang_val.endswith(")"):
                        lang_val = lang_val[1:-1].strip()
                    result["lang"] = lang_val or None
            except Exception:
                result["validation"]["notes"].append("Could not read /Lang")

            if "/StructTreeRoot" not in root:
                result["validation"]["errors"].append("No /StructTreeRoot (PDF is likely untagged)")
                return result

            result["has_tags"] = True
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

            nodes = []
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
                    "id": node_id,
                    "role": role,
                    "depth": depth,
                    "children": [],
                    "alt": None,
                    "actual_text": None
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
                    if isinstance(node, pikepdf.Dictionary) and "/K" in node:
                        child = node["/K"]

                        if isinstance(child, pikepdf.Array):
                            for c in child:
                                if isinstance(c, pikepdf.Dictionary):
                                    out_node["children"].append(walk(c, depth + 1))
                                else:
                                    out_node["children"].append({
                                        "type": "leaf",
                                        "value": _pike_to_py(c)
                                    })
                        elif isinstance(child, pikepdf.Dictionary):
                            out_node["children"].append(walk(child, depth + 1))
                        else:
                            out_node["children"].append({
                                "type": "leaf",
                                "value": _pike_to_py(child)
                            })
                except Exception:
                    result["validation"]["notes"].append(f"Failed walking children at depth {depth}")

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
    """
    Returns (text, confidence)
    """
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
        data = pytesseract.image_to_data(img, lang=lang, output_type=pytesseract.Output.DICT)
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
    """
    OCR only images that are likely to contain readable text.
    """
    for occ in image_occurrences:
        asset_id = occ["asset_id"]
        b = asset_bytes_global.get(asset_id)
        if not b:
            continue

        try:
            img = Image.open(io.BytesIO(b))
            w, h = img.size
            if (w * h) < min_px:
                continue
        except Exception:
            pass

        text, conf = ocr_image_bytes(b, lang="eng")
        occ["ocr_text"] = text
        occ["ocr_confidence"] = conf


# -----------------------------
# Main extraction
# -----------------------------

def extract_document_json(pdf_path: str, run_ocr: bool = True) -> Dict[str, Any]:
    text_blocks, reading_order = extract_pdfminer_blocks(pdf_path)

    doc = fitz.open(pdf_path)

    pdf_meta = doc.metadata or {}
    title = pdf_meta.get("title")
    author = pdf_meta.get("author")
    subject = pdf_meta.get("subject")
    keywords = pdf_meta.get("keywords")

    bookmarks = extract_bookmarks(doc)

    pages: List[Dict[str, Any]] = []
    text_spans: List[Dict[str, Any]] = []

    all_image_assets: Dict[str, Any] = {}
    all_image_occurrences: List[Dict[str, Any]] = []
    all_form_fields: List[Dict[str, Any]] = []
    all_links: List[Dict[str, Any]] = []
    all_graphics: List[Dict[str, Any]] = []
    all_asset_bytes: Dict[str, bytes] = {}

    for page_index in range(doc.page_count):
        page = doc.load_page(page_index)

        pages.append({
            "page_index": page_index,
            "width": float(page.rect.width),
            "height": float(page.rect.height),
            "rotation": int(page.rotation)
        })

        text_dict = page.get_text("dict")
        span_counter = 0

        for block in text_dict.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    bbox = span.get("bbox")
                    txt = span.get("text", "")

                    if not bbox or not txt.strip():
                        continue

                    detected_lang = detect_language_safe(txt)

                    span_id = f"span_p{page_index}_s{span_counter}"
                    span_counter += 1

                    text_spans.append({
                        "id": span_id,
                        "page_index": page_index,
                        "bbox": [float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])],
                        "text": txt,
                        "detected_language": detected_lang,
                        "font": {
                            "name": span.get("font", ""),
                            "size": float(span.get("size", 0.0)),
                            "flags": span.get("flags", None)
                        },
                        "color": {
                            "fill_rgb": int_color_to_rgb(span.get("color"))
                        }
                    })

        assets, occurrences, asset_bytes_map = extract_images(page, doc, page_index)

        for aid, asset in assets.items():
            all_image_assets[aid] = asset

        for aid, img_bytes in asset_bytes_map.items():
            all_asset_bytes[aid] = img_bytes

        all_image_occurrences.extend(occurrences)
        all_links.extend(extract_links(page, page_index))
        all_graphics.extend(extract_graphics(page, page_index))
        all_form_fields.extend(extract_form_fields(page, page_index))

    compute_contrast_for_spans(doc, text_spans, scale=2.0)
    doc.close()

    if run_ocr:
        run_ocr_on_image_occurrences(all_image_occurrences, all_asset_bytes)

    text_blocks = align_blocks_to_spans(text_blocks, text_spans, pages)
    inferred_language = infer_document_language(text_spans)
    heading_candidates = detect_heading_candidates(text_spans)
    file_hash = sha256_file(pdf_path)
    structure = extract_structure_pikepdf(pdf_path)

    out = {
        "document": {
            "metadata": {
                "filename": Path(pdf_path).name,
                "title": title,
                "author": author,
                "subject": subject,
                "keywords": keywords,
                "file_hash_sha256": file_hash,
                "page_count": len(pages),
                "coordinate_system": {
                    "units": "pt",
                    "note": "PyMuPDF bboxes stored in document.*.bbox; pdfminer raw bboxes stored in text_blocks[].bbox_pdfminer"
                }
            },
            "pages": pages,
            "text_spans": text_spans,
            "text_blocks": text_blocks,
            "images": {
                "assets": list(all_image_assets.values()),
                "occurrences": all_image_occurrences
            },
            "graphics": all_graphics,
            "links": all_links,
            "bookmarks": bookmarks,
           "form_fields": all_form_fields,
            "structure": structure,
            "inferred_language": inferred_language,
            "heading_candidates": heading_candidates,
            "reading_order": {
                "source": "pdfminer",
                "order": reading_order,
                "note": "reading order is block IDs"
            }
        }
    }

    return out


def main():
    import argparse
    parser = argparse.ArgumentParser(description="AccessEd parsing: Phase 1 + 2 + 2.5")
    parser.add_argument("pdf_path", help="Path to input PDF")
    parser.add_argument("--out", default="out3.json", help="Output JSON path")
    args = parser.parse_args()

    data = extract_document_json(args.pdf_path)
    Path(args.out).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote JSON to: {args.out}")


if __name__ == "__main__":
    main()