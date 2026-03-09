import json
import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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
    # flip y using page height
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


def contrast_ratio(fg_rgb: List[int], bg_rgb: List[int]) -> float:
    L1 = relative_luminance(fg_rgb)
    L2 = relative_luminance(bg_rgb)
    lighter = max(L1, L2)
    darker = min(L1, L2)
    return (lighter + 0.05) / (darker + 0.05)


def is_large_text(font_size_pt: float) -> bool:
    # WCAG large text: >= 18pt normal OR >= 14pt bold
    # We don't reliably know bold yet -> treat >=18pt as large; later upgrade using flags.
    return font_size_pt >= 18.0
def estimate_background_rgb_for_bbox(page: fitz.Page, bbox: List[float], scale: float = 2.0, grid: int = 8) -> Optional[List[int]]:
    """
    Render page to an image and sample pixels under bbox.
    Returns median RGB from sampled pixels.
    - scale: render scaling (2.0 = sharper)
    - grid: grid x grid samples inside bbox
    """
    try:
        mat = fitz.Matrix(scale, scale)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        if pix.n < 3:
            return None
    except Exception:
        return None

    # Scale bbox to pixel coords
    x0, y0, x1, y1 = bbox
    px0 = int(max(0, min(pix.width - 1, round(x0 * scale))))
    py0 = int(max(0, min(pix.height - 1, round(y0 * scale))))
    px1 = int(max(0, min(pix.width - 1, round(x1 * scale))))
    py1 = int(max(0, min(pix.height - 1, round(y1 * scale))))

    if px1 <= px0 or py1 <= py0:
        return None

    # shrink a bit to avoid sampling glyph pixels too much
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
    # group spans by page
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
                # fallback: assume white background
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

    return image_assets, image_occurrences,asset_bytes_map


def extract_links(page: fitz.Page, page_index: int):
    links = []
    link_counter = 0

    for l in page.get_links():
        rect = l.get("from")
        if rect is None:
            continue

        link_id = f"link_p{page_index}_{link_counter}"
        link_counter += 1

        if "uri" in l:
            kind = "uri"
            target = l.get("uri")
        elif "page" in l:
            kind = "internal"
            target = f"page_{l.get('page')}"
        else:
            kind = "unknown"
            target = None

        links.append({
            "id": link_id,
            "page_index": page_index,
            "bbox": [float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)],
            "kind": kind,
            "target": target
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
                "span_ids": []  # Phase 2.5 fills this
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
    Also adds "bbox" for the block in PyMuPDF coordinates (for consistency).
    """
    # index spans by page
    spans_by_page: Dict[int, List[Dict[str, Any]]] = {}
    for s in text_spans:
        spans_by_page.setdefault(s["page_index"], []).append(s)

    # Map page heights
    page_height: Dict[int, float] = {p["page_index"]: float(p["height"]) for p in pages}

    for blk in text_blocks:
        pno = blk["page_index"]
        h = page_height.get(pno)
        if h is None:
            continue

        bbox_pym = pdfminer_bbox_to_pymupdf_bbox(blk["bbox_pdfminer"], h)
        blk["bbox"] = bbox_pym  # consistent bbox for later annotation

        candidates = spans_by_page.get(pno, [])
        matched_ids = []

        for sp in candidates:
            cx, cy = center_of_bbox(sp["bbox"])
            if point_in_bbox(cx, cy, bbox_pym, margin=3.0):
                matched_ids.append(sp["id"])

        # Optional: sort matched spans by y then x (reading-ish order)
        def span_sort_key(span_id: str):
            # find span
            s = next((x for x in candidates if x["id"] == span_id), None)
            if not s:
                return (0, 0)
            x0, y0, x1, y1 = s["bbox"]
            return (y0, x0)

        matched_ids.sort(key=span_sort_key)

        blk["span_ids"] = matched_ids

    return text_blocks

def _pike_to_py(obj):
    """
    Convert pikepdf objects into JSON-safe Python types.
    Keep it conservative: strings, numbers, booleans, dict, list, None.
    """
    try:
        # pikepdf.Name like /Figure, /P etc.
        if isinstance(obj, pikepdf.Name):
            return str(obj)[1:]  # remove leading "/"
        # pikepdf.String or regular Python str
        if isinstance(obj, (pikepdf.String, str)):
            return str(obj)
        # numbers/bools
        if isinstance(obj, (int, float, bool)):
            return obj
        # null
        if obj is None:
            return None
        # arrays
        if isinstance(obj, pikepdf.Array):
            return [_pike_to_py(x) for x in obj]
        # dictionaries
        if isinstance(obj, pikepdf.Dictionary):
            out = {}
            for k, v in obj.items():
                out[str(k)[1:] if isinstance(k, pikepdf.Name) else str(k)] = _pike_to_py(v)
            return out
        # fallback
        return str(obj)
    except Exception:
        return str(obj)


def extract_structure_pikepdf(pdf_path: str):
    """
    Extract:
      - /Lang (document language)
      - /StructTreeRoot presence (tagged PDF)
      - /RoleMap (if present)
      - a simplified structure tree (roles + children + alt/actualtext if present)
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

            # Language metadata
            try:
                if "/Lang" in root:
                    result["lang"] = str(root["/Lang"])
            except Exception:
                result["validation"]["notes"].append("Could not read /Lang")

            # StructTreeRoot = tag tree root
            if "/StructTreeRoot" not in root:
                result["validation"]["errors"].append("No /StructTreeRoot (PDF is likely untagged)")
                return result

            result["has_tags"] = True
            struct_root = root["/StructTreeRoot"]

            # RoleMap: mapping custom tags -> standard tags
            try:
                if "/RoleMap" in struct_root:
                    result["role_map"] = _pike_to_py(struct_root["/RoleMap"])
            except Exception:
                result["validation"]["notes"].append("Could not read /RoleMap")

            # K = kids of structure tree root (can be array or single dict)
            if "/K" not in struct_root:
                result["validation"]["errors"].append("StructTreeRoot has no /K children")
                result["tree"] = []
                return result

            kids = struct_root["/K"]
            # normalize kids to list
            if not isinstance(kids, pikepdf.Array):
                kids = pikepdf.Array([kids])

            nodes = []
            node_counter = 0

            def walk(node, depth=0):
                nonlocal node_counter
                node_id = f"node_{node_counter}"
                node_counter += 1

                # Role /S (e.g., /P, /H1, /Figure)
                role = None
                try:
                    if isinstance(node, pikepdf.Dictionary) and "/S" in node:
                        role = str(node["/S"])[1:]  # drop "/"
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

                # /Alt and /ActualText sometimes exist for tagged figures/spans
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

                # Traverse children /K
                try:
                    if isinstance(node, pikepdf.Dictionary) and "/K" in node:
                        child = node["/K"]

                        # /K can be: int (MCID), dict, array, or mixed
                        if isinstance(child, pikepdf.Array):
                            for c in child:
                                if isinstance(c, pikepdf.Dictionary):
                                    out_node["children"].append(walk(c, depth + 1))
                                else:
                                    # MCID or other leaf
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
                    # sometimes kids are MCIDs directly
                    nodes.append({"type": "leaf", "value": _pike_to_py(k)})

            result["tree"] = nodes
            return result

    except Exception as e:
        result["validation"]["errors"].append(f"pikepdf failed: {type(e).__name__}: {e}")
        return result

def ocr_image_bytes(img_bytes: bytes, lang: str = "eng"):
    """
    Returns (text, confidence)
    Confidence here is a simple average word confidence (0-100) when available.
    If not available, returns None for confidence.
    """
    try:
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    except Exception:
        return None, None

    # Get text
    try:
        text = pytesseract.image_to_string(img, lang=lang).strip()
    except Exception:
        return None, None

    # Get confidence (optional)
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
    min_px is width*height threshold for skipping tiny icons.
    """
    for occ in image_occurrences:
        asset_id = occ["asset_id"]
        b = asset_bytes_global.get(asset_id)
        if not b:
            continue

        # Optional: skip tiny images based on asset metadata (if you have it)
        # We'll estimate size using Pillow quickly
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
# Main extraction: Phase 1 + 2 + 2.5
# -----------------------------

def extract_interactivity_pikepdf(pdf_path: str) -> Dict[str, Any]:
    """
    Extract interactivity signals needed for WCAG 2.1.x checks:
      - JavaScript presence and triggers (2.1.2, 2.1.4 risk signals)
      - AcroForm fields with name, type, tab index, and flags (2.1.1, 3.3.2)
      - Tab order per page (2.1.1)

    Returns:
    {
        "has_javascript": bool,
        "javascript_triggers": [
            {"trigger": str, "location": str}
        ],
        "has_acroform": bool,
        "acroform_fields": [
            {
                "id":         str,   # field partial name /T
                "type":       str,   # /Tx, /Btn, /Ch, /Sig
                "flags":      int,   # raw /Ff bitmask
                "read_only":  bool,
                "required":   bool,
                "tooltip":    str | None,  # /TU
                "page_index": int | None
            }
        ],
        "tab_order": [
            {"page_index": int, "tabs": str | None}
            # tabs: "R" (row), "C" (column), "S" (structure), None (unspecified)
        ],
        "has_tab_order": bool   # True if ANY page explicitly sets /Tabs
    }
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
            # 1) Document-level OpenAction
            try:
                if "/OpenAction" in root:
                    action = root["/OpenAction"]
                    if isinstance(action, pikepdf.Dictionary):
                        s = action.get("/S")
                        if s and str(s) in ("/JavaScript", "/JS"):
                            result["has_javascript"] = True
                            result["javascript_triggers"].append({
                                "trigger":  "OpenAction",
                                "location": "document"
                            })
            except Exception:
                pass

            # 2) Document-level /AA (Additional Actions)
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

            # 3) Named JavaScript actions in /Names tree
            try:
                if "/Names" in root:
                    names = root["/Names"]
                    if isinstance(names, pikepdf.Dictionary) and "/JavaScript" in names:
                        result["has_javascript"] = True
                        result["javascript_triggers"].append({
                            "trigger":  "Names/JavaScript",
                            "location": "document"
                        })
            except Exception:
                pass

            # 4) Per-page /AA
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

            # ── Tab order per page ───────────────────────────────────────────
            try:
                for page_index, page in enumerate(pdf.pages):
                    tabs_val = None
                    if "/Tabs" in page:
                        tabs_val = str(page["/Tabs"])[1:]  # strip leading "/"
                        result["has_tab_order"] = True

                    result["tab_order"].append({
                        "page_index": page_index,
                        "tabs":       tabs_val
                    })
            except Exception:
                pass

            # ── Submit action detection ──────────────────────────────────────
            # Detect /SubmitForm actions on buttons or document-level actions
            result["submit_actions"] = []
            try:
                if "/AcroForm" in root:
                    acroform = root["/AcroForm"]
                    if isinstance(acroform, pikepdf.Dictionary):
                        fields_arr = acroform.get("/Fields")
                        if fields_arr and isinstance(fields_arr, pikepdf.Array):

                            def find_submit(field_obj, counter=[0]):
                                if not isinstance(field_obj, pikepdf.Dictionary):
                                    return
                                # Check /A (action) on the field
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
                                # Recurse into Kids
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

                def process_field(field_obj: pikepdf.Dictionary, page_index: int | None):
                    nonlocal field_counter

                    # Recurse into Kids (field groups)
                    if "/Kids" in field_obj:
                        kids = field_obj["/Kids"]
                        if isinstance(kids, pikepdf.Array):
                            for kid in kids:
                                try:
                                    if isinstance(kid, pikepdf.Dictionary):
                                        process_field(kid, page_index)
                                    else:
                                        # indirect ref
                                        process_field(kid.get_object(), page_index)
                                except Exception:
                                    pass
                        return

                    field_id = f"field_{field_counter}"
                    field_counter += 1

                    # Field type /FT
                    ft = None
                    try:
                        if "/FT" in field_obj:
                            ft = str(field_obj["/FT"])[1:]  # Tx, Btn, Ch, Sig
                    except Exception:
                        pass

                    # Partial field name /T
                    name = None
                    try:
                        if "/T" in field_obj:
                            name = str(field_obj["/T"])
                    except Exception:
                        pass

                    # Tooltip /TU
                    tooltip = None
                    try:
                        if "/TU" in field_obj:
                            tooltip = str(field_obj["/TU"])
                    except Exception:
                        pass

                    # Field flags /Ff
                    ff = 0
                    try:
                        if "/Ff" in field_obj:
                            ff = int(field_obj["/Ff"])
                    except Exception:
                        pass

                    read_only = bool(ff & 1)   # bit 1
                    required  = bool(ff & 2)   # bit 2

                    # Page reference via /P
                    pg_index = page_index
                    try:
                        if "/P" in field_obj:
                            p_ref = field_obj["/P"]
                            # find page index by matching object
                            for i, pg in enumerate(pdf.pages):
                                if pg.objgen == p_ref.objgen:
                                    pg_index = i
                                    break
                    except Exception:
                        pass

                    # Per-field Additional Actions /AA (validation, format, etc.)
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
                                    field_aa[str(aa_key)[1:]] = {
                                        "has_javascript": is_js
                                    }
                    except Exception:
                        pass

                    # Appearance state /AS (checked/unchecked for Btn fields)
                    appearance_state = None
                    try:
                        if "/AS" in field_obj:
                            appearance_state = str(field_obj["/AS"])[1:]  # strip "/"
                    except Exception:
                        pass

                    result["acroform_fields"].append({
                        "id":               field_id,
                        "name":             name,
                        "type":             ft,
                        "flags":            ff,
                        "read_only":        read_only,
                        "required":         required,
                        "tooltip":          tooltip,
                        "page_index":       pg_index,
                        "validation_actions": field_aa,
                        "appearance_state": appearance_state,  # e.g. "Yes", "Off", None
                    })

                for field_ref in fields_array:
                    try:
                        if isinstance(field_ref, pikepdf.Dictionary):
                            process_field(field_ref, None)
                        else:
                            process_field(field_ref.get_object(), None)
                    except Exception:
                        pass

            except Exception:
                pass

    except Exception as e:
        result["error"] = f"pikepdf failed: {type(e).__name__}: {e}"

    return result
    
def extract_document_json(pdf_path: str ,run_ocr: bool = True) -> Dict[str, Any]:
    # Phase 2 first (uses file path)
    text_blocks, reading_order = extract_pdfminer_blocks(pdf_path)

    # Phase 1 (visual truth)
    doc = fitz.open(pdf_path)

    pages: List[Dict[str, Any]] = []
    text_spans: List[Dict[str, Any]] = []

    all_image_assets: Dict[str, Any] = {}
    all_image_occurrences: List[Dict[str, Any]] = []

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

        # spans
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

                    span_id = f"span_p{page_index}_s{span_counter}"
                    span_counter += 1

                    text_spans.append({
                        "id": span_id,
                        "page_index": page_index,
                        "bbox": [float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])],
                        "text": txt,
                        "font": {
                            "name": span.get("font", ""),
                            "size": float(span.get("size", 0.0)),
                            "flags": span.get("flags", None)
                        },
                        "color": {
                            "fill_rgb": int_color_to_rgb(span.get("color"))
                        }
                    })

        # images
        assets, occurrences, asset_bytes_map = extract_images(page, doc, page_index)

        for aid, asset in assets.items():
            all_image_assets[aid] = asset

        for aid, img_bytes in asset_bytes_map.items():
            all_asset_bytes[aid] = img_bytes

        all_image_occurrences.extend(occurrences)

        # links
        all_links.extend(extract_links(page, page_index))

        # graphics
        all_graphics.extend(extract_graphics(page, page_index))
    # Phase 5: contrast (needs open doc)
    compute_contrast_for_spans(doc, text_spans, scale=2.0)

    doc.close()
    # Phase 4: OCR (fills image_occurrences[].ocr_text and .ocr_confidence)
    if run_ocr:
       run_ocr_on_image_occurrences(all_image_occurrences, all_asset_bytes)

    # Phase 2.5 alignment
    text_blocks = align_blocks_to_spans(text_blocks, text_spans, pages)

    file_hash = sha256_file(pdf_path)
    structure = extract_structure_pikepdf(pdf_path)
    interactivity = extract_interactivity_pikepdf(pdf_path)

    out = {
        "document": {
            "metadata": {
                "filename": Path(pdf_path).name,
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
            "structure": structure,
            "interactivity": interactivity,
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