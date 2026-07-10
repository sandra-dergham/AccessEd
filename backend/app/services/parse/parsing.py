import fitz
from typing import Any, List, Dict
from pathlib import Path 
import hashlib

from .annotators import annotate_decorative_text, annotate_graphics_non_text_contrast, annotate_logo_like_text, annotate_media_alternatives, annotate_resize_risk, annotate_text_in_image_context, annotate_ui_labels, annotate_widgets_non_text_contrast
from .defaults import default_presentation_semantics, default_resize_risk
from .extractors.interactivity import extract_interactivity_pikepdf
from .extractors.media import extract_embedded_files_pikepdf, extract_media_annotations_pikepdf, extract_media_occurrences
from .extractors.page_elements import extract_bookmarks, extract_form_fields, extract_graphics, extract_images, extract_links, extract_pdfminer_blocks, extract_widgets
from .extractors.structure import extract_figure_nodes_from_structure, extract_structure_pikepdf
from .ocr import run_ocr_on_image_occurrences
from ..utils.color_helper import compute_contrast_for_spans, contrast_ratio, int_color_to_rgb
from ..utils.font_helper import detect_heading_candidates
from ..utils.geometry_helper import align_blocks_to_spans
from ..utils.graphics_helper import attach_page_index_to_figures, map_figures_to_images_by_order, map_figures_to_images_by_page_and_mcid, map_single_figure_alt_to_single_image
from ..utils.text_helper import detect_language_safe, infer_document_language
from ..utils.widget_helper import _read_mk_border_color_pikepdf

def sha256_file(path: str) -> str:
    data = Path(path).read_bytes()
    return hashlib.sha256(data).hexdigest()


def extract_document_json(pdf_path: str, run_ocr: bool = True) -> Dict[str, Any]:
    text_blocks, reading_order = extract_pdfminer_blocks(pdf_path)

    doc = fitz.open(pdf_path)

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
    all_form_fields:       List[Dict[str, Any]] = []  

    for page_index in range(doc.page_count):
        page = doc.load_page(page_index)

        pages.append({
            "page_index": page_index,
            "width":      float(page.rect.width),
            "height":     float(page.rect.height),
            "rotation":   int(page.rotation)
        })

        all_widgets.extend(extract_widgets(page, page_index))
        all_form_fields.extend(extract_form_fields(page, page_index))  

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

                    detected_lang = detect_language_safe(txt)  

                    span_id = f"span_p{page_index}_s{span_counter}"
                    span_counter += 1

                    text_spans.append({
                        "id":                span_id,
                        "page_index":        page_index,
                        "bbox":              [float(bbox[0]), float(bbox[1]),
                                              float(bbox[2]), float(bbox[3])],
                        "text":              txt,
                        "detected_language": detected_lang,        
                        "font": {
                            "name":  span.get("font", ""),
                            "size":  float(span.get("size", 0.0)),
                            "flags": span.get("flags", None)
                        },
                        "color": {
                            "fill_rgb": int_color_to_rgb(span.get("color"))
                        },
                        "layout": {                                
                            "block_index": block_idx,
                            "line_index":  line_idx
                        },
                        "presentation_semantics": default_presentation_semantics(),  
                        "resize_risk":            default_resize_risk(),              
                    })

        assets, occurrences, asset_bytes_map = extract_images(page, doc, page_index)
        for aid, asset in assets.items():
            all_image_assets[aid] = asset
        for aid, img_bytes in asset_bytes_map.items():
            all_asset_bytes[aid] = img_bytes
        all_image_occurrences.extend(occurrences)

        all_links.extend(extract_links(page, page_index))
        all_graphics.extend(extract_graphics(page, page_index))

    compute_contrast_for_spans(doc, text_spans, scale=2.0)
    annotate_graphics_non_text_contrast(doc, all_graphics, scale=2.0)
    annotate_widgets_non_text_contrast(doc, all_widgets, scale=2.0)

    # Fallback: if pixmap border contrast fails or is missing,
    # check /MK /BC in case the corrector set it
    for w in all_widgets:
        ntc = w.get("non_text_contrast", {})
        if ntc.get("passes_3_1") is True:
            continue  # already passing, no need for fallback
        bbox = w.get("bbox")
        page_idx = w.get("page_index")
        if bbox is None or page_idx is None:
            continue
        mk_rgb = _read_mk_border_color_pikepdf(pdf_path, page_idx, bbox)
        if mk_rgb is not None:
            adj = ntc.get("adjacent_rgb", [255, 255, 255])
            ratio = contrast_ratio(mk_rgb, adj)
            ntc["border_rgb"] = mk_rgb
            ntc["contrast_against_border"] = float(round(ratio, 3))
            ntc["passes_3_1"] = ratio >= 3.0
            ntc["method"] = (ntc.get("method", "") + "_mk_bc_fallback")

    media_occurrences = []
    media_occurrences.extend(extract_media_occurrences(doc, text_spans))
    media_occurrences.extend(extract_media_annotations_pikepdf(pdf_path, pages))
    media_occurrences.extend(extract_embedded_files_pikepdf(pdf_path))
    annotate_media_alternatives(media_occurrences, text_spans)

    bookmarks = extract_bookmarks(doc)  

    doc.close()

    if run_ocr:
        run_ocr_on_image_occurrences(all_image_occurrences, all_asset_bytes)

    annotate_text_in_image_context(text_spans, all_image_occurrences)
    annotate_logo_like_text(text_spans, pages)
    annotate_decorative_text(text_spans)
    annotate_ui_labels(text_spans, all_widgets)
    annotate_resize_risk(text_spans, all_graphics, all_widgets, pages)

    text_blocks = align_blocks_to_spans(text_blocks, text_spans, pages)

    inferred_language  = infer_document_language(text_spans)
    heading_candidates = detect_heading_candidates(text_spans)

    file_hash = sha256_file(pdf_path)
    structure = extract_structure_pikepdf(pdf_path)

    structure["figures"] = extract_figure_nodes_from_structure(structure)
    attach_page_index_to_figures(structure["figures"], pdf_path)
    map_single_figure_alt_to_single_image(structure, all_image_occurrences)
    map_figures_to_images_by_page_and_mcid(structure, all_image_occurrences)
    map_figures_to_images_by_order(structure, all_image_occurrences)

    interactivity = extract_interactivity_pikepdf(pdf_path)

    out = {
        "document": {
            "metadata": {
                "filename":          Path(pdf_path).name,
                "title":             title,     
                "author":            author,    
                "subject":           subject,   
                "keywords":          keywords,  
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
            "bookmarks":          bookmarks,        
            "form_fields":        all_form_fields,  
            "widgets":            all_widgets,
            "media": {
                "occurrences": media_occurrences
            },
            "structure":          structure,
            "interactivity":      interactivity,    
            "inferred_language":  inferred_language,    
            "heading_candidates": heading_candidates,   
            "reading_order": {
                "source": "pdfminer",
                "order":  reading_order,
                "note":   "reading order is block IDs"
            }
        }
    }

    return out


