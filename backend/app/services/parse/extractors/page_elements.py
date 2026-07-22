from typing import Any, Dict, List

import fitz
from pdfminer.high_level import extract_pages
from pdfminer.layout import (
    LTAnno,
    LTChar,
    LTTextBoxHorizontal,
    LTTextLineHorizontal,
)
import hashlib

from ...utils.color_helper import (
    float_rgb_to_int,
    
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def extract_widgets(page: fitz.Page, page_index: int) -> List[Dict[str, Any]]:
    widgets = []
    w_counter = 0

    try:
        ws = page.widgets()
    except Exception:
        ws = []

    if not ws:
        return widgets

    for w in ws:
        try:
            rect = w.rect
        except Exception:
            continue

        try:
            flags = int(w.field_flags)
        except Exception:
            flags = 0

        try:
            widget_type = str(w.field_type)
        except Exception:
            widget_type = None

        try:
            field_name = w.field_name
        except Exception:
            field_name = None

        is_read_only = bool(flags & 1)

        widgets.append({
            "id": f"widget_p{page_index}_{w_counter}",
            "page_index": page_index,
            "bbox": [float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)],
            "field_name": field_name,
            "field_type": widget_type,
            "field_flags": flags,
            "ui_state": "inactive" if is_read_only else "active"
        })
        w_counter += 1

    return widgets



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

        # Read /Contents from annotation xref (PyMuPDF's get_links doesn't expose it)
        annot_contents = None
        xref = l.get("xref")
        if xref:
            try:
                ct = page.parent.xref_get_key(xref, "Contents")
                if ct[0] == "string":
                    val = ct[1]
                    # xref_get_key returns PDF string literals with parentheses
                    if val.startswith("(") and val.endswith(")"):
                        val = val[1:-1]
                    annot_contents = val.strip() or None
            except Exception:
                pass

        links.append({
            "id":         link_id,
            "page_index": page_index,
            "bbox":       [float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)],
            "kind":       link_type,
            "type":       link_type,
            "target":     target,
            "uri":        uri,
            "contents":   annot_contents,
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
