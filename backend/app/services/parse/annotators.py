from typing import Any, List, Dict

import fitz

from .defaults import default_non_text_contrast, default_presentation_semantics
from ..utils.color_helper import contrast_ratio

from ..utils.graphics_helper import is_likely_layout_or_decorative_graphic, sample_outside_ring_rgb, sample_widget_border_rgb
from ..utils.text_helper import _normalize_repeat_text, is_same_line
from ..utils.geometry_helper import(
    _horizontal_gap, bbox_contains, bbox_exceeds_container,bbox_intersects,
    _vertical_gap, bbox_width, estimate_resized_text_bbox_200, find_line_box_container, 
    looks_like_paragraph_continuation,bbox_height, intersection_ratio_of_span
)

from ..utils.patterns import (

    CAPTION_NEGATIVE_RE, CAPTION_POSITIVE_RE, LIVE_KEYWORDS, TRANSCRIPT_NEGATIVE_RE,TRANSCRIPT_POSITIVE_RE,ALT_TEXT_KEYWORDS,
    AUDIO_DESC_POSITIVE_RE,AUDIO_DESC_NEGATIVE_RE,
)





def annotate_media_alternatives(
    media_occurrences: List[Dict[str, Any]],
    text_spans: List[Dict[str, Any]],
    max_h_gap: float = 220.0,
    max_v_gap: float = 80.0,
):
    """
    Add transcript / captions / audio-description / media-alternative evidence
    to each detected media item.

    - For page-located media: search nearby text on the same page.
    - For document-level embedded media: search all document text.
    """
    spans_by_page: Dict[int, List[Dict[str, Any]]] = {}
    for sp in text_spans:
        spans_by_page.setdefault(sp["page_index"], []).append(sp)

    for media in media_occurrences:
        page_index = media.get("page_index")
        mb = media.get("bbox")

        nearby_ids: List[str] = []
        found_alt = False
        candidate_spans: List[Dict[str, Any]] = []

        if page_index is not None and mb:
            for sp in spans_by_page.get(page_index, []):
                sb = sp.get("bbox")
                st = (sp.get("text") or "").strip().lower()
                if not sb or not st:
                    continue

                h_gap = _horizontal_gap(mb, sb)
                v_gap = _vertical_gap(mb, sb)

                if h_gap <= max_h_gap and v_gap <= max_v_gap:
                    candidate_spans.append(sp)
                    if sp.get("id"):
                        nearby_ids.append(sp["id"])

        else:
            for sp in text_spans:
                st = (sp.get("text") or "").strip().lower()
                if not st:
                    continue
                candidate_spans.append(sp)
                if sp.get("id"):
                    nearby_ids.append(sp["id"])

        for sp in candidate_spans:
            st = (sp.get("text") or "").strip().lower()

            if any(keyword in st for keyword in ALT_TEXT_KEYWORDS):
                found_alt = True

            if TRANSCRIPT_POSITIVE_RE.search(st) and not TRANSCRIPT_NEGATIVE_RE.search(st):
                media["has_detectable_transcript"] = True
                media["has_detectable_media_alternative"] = True

            if CAPTION_POSITIVE_RE.search(st) and not CAPTION_NEGATIVE_RE.search(st):
                media["has_detectable_captions"] = True
                media["has_detectable_media_alternative"] = True

            if AUDIO_DESC_POSITIVE_RE.search(st) and not AUDIO_DESC_NEGATIVE_RE.search(st):
                media["has_detectable_audio_description"] = True
                media["has_detectable_media_alternative"] = True

            if any(k in st for k in LIVE_KEYWORDS):
                media["looks_live"] = True

        media["nearby_text_ids"] = nearby_ids
        media["has_detectable_media_alternative"] = (
    media.get("has_detectable_media_alternative", False) or found_alt
)


def annotate_ui_labels(
    text_spans: List[Dict[str, Any]],
    widgets: List[Dict[str, Any]],
    near_margin: float = 12.0
):
    widgets_by_page: Dict[int, List[Dict[str, Any]]] = {}
    for w in widgets:
        widgets_by_page.setdefault(w["page_index"], []).append(w)

    for span in text_spans:
        sem = span.setdefault("presentation_semantics", default_presentation_semantics())
        bbox = span.get("bbox")
        page_index = span.get("page_index")

        if not bbox:
            continue

        for widget in widgets_by_page.get(page_index, []):
            widget_bbox = widget.get("bbox")
            if not widget_bbox:
                continue

            if bbox_intersects(bbox, widget_bbox, margin=near_margin):
                sem["is_ui_label"] = True
                sem["ui_component_type"] = widget.get("field_type")
                sem["ui_state"] = widget.get("ui_state")
                break



def annotate_text_in_image_context(
    text_spans: List[Dict[str, Any]],
    image_occurrences: List[Dict[str, Any]],
    overlap_threshold: float = 0.15,
    near_margin: float = 3.0
):
    images_by_page: Dict[int, List[Dict[str, Any]]] = {}
    for img in image_occurrences:
        images_by_page.setdefault(img["page_index"], []).append(img)

    for span in text_spans:
        sem = span.setdefault("presentation_semantics", default_presentation_semantics())
        span_bbox = span.get("bbox")
        page_index = span.get("page_index")

        if not span_bbox:
            continue

        overlapping_ids = []
        best_ratio = 0.0

        for img in images_by_page.get(page_index, []):
            img_bbox = img.get("bbox")
            if not img_bbox:
                continue

            ratio = intersection_ratio_of_span(span_bbox, img_bbox)
            near = bbox_intersects(span_bbox, img_bbox, margin=near_margin)

            if ratio >= overlap_threshold or (ratio > 0 and near):
                overlapping_ids.append(img["id"])
                best_ratio = max(best_ratio, ratio)

        if overlapping_ids:
            sem["is_text_in_image_context"] = True
            sem["image_context_confidence"] = float(round(max(best_ratio, 0.7), 3))
            sem["image_context_reason"] = "Text span overlaps or is embedded in an image region."
            sem["overlapping_image_ids"] = overlapping_ids


def annotate_logo_like_text(
    text_spans: List[Dict[str, Any]],
    pages: List[Dict[str, Any]]
):
    page_heights = {p["page_index"]: float(p["height"]) for p in pages}

    for span in text_spans:
        sem = span.setdefault("presentation_semantics", default_presentation_semantics())
        text = (span.get("text") or "").strip()
        bbox = span.get("bbox")
        font_size = float(span.get("font", {}).get("size", 0.0))
        page_index = span.get("page_index")

        if not text or not bbox:
            continue

        page_height = page_heights.get(page_index, 0.0)
        if page_height <= 0:
            continue

        y0 = bbox[1]
        in_top_band = y0 <= page_height * 0.20
        short_text = len(text) <= 25
        one_or_two_words = len(text.split()) <= 2
        largeish = font_size >= 14.0
        uppercase_like = text.isupper() and any(ch.isalpha() for ch in text)

        score = 0.0
        reasons = []

        if in_top_band:
            score += 0.35
            reasons.append("top_of_page")
        if short_text and one_or_two_words:
            score += 0.25
            reasons.append("short_brand_like_text")
        if largeish:
            score += 0.20
            reasons.append("large_font")
        if uppercase_like:
            score += 0.20
            reasons.append("uppercase_style")

        if score >= 0.75:
            sem["is_logo_text"] = True
            sem["logo_confidence"] = float(round(score, 3))
            sem["logo_reason"] = ", ".join(reasons)


def annotate_decorative_text(text_spans: List[Dict[str, Any]]):
    text_counts: Dict[str, int] = {}
    for span in text_spans:
        norm = _normalize_repeat_text(span.get("text", ""))
        if norm:
            text_counts[norm] = text_counts.get(norm, 0) + 1

    for span in text_spans:
        sem = span.setdefault("presentation_semantics", default_presentation_semantics())
        text = (span.get("text") or "").strip()
        bbox = span.get("bbox")
        font_size = float(span.get("font", {}).get("size", 0.0))
        norm = _normalize_repeat_text(text)

        if not text or not bbox:
            continue

        repeated = text_counts.get(norm, 0) >= 3
        short_text = len(text) <= 12
        huge_text = font_size >= 28.0
        uppercase_like = text.isupper() and any(ch.isalpha() for ch in text)

        score = 0.0
        reasons = []

        if repeated:
            score += 0.35
            reasons.append("repeated_text")
        if short_text:
            score += 0.15
            reasons.append("short_text")
        if huge_text:
            score += 0.30
            reasons.append("very_large_text")
        if uppercase_like:
            score += 0.20
            reasons.append("uppercase_style")

        if score >= 0.80:
            sem["is_decorative_text"] = True
            sem["decorative_confidence"] = float(round(score, 3))
            sem["decorative_reason"] = ", ".join(reasons)



def annotate_graphics_non_text_contrast(
    doc: fitz.Document,
    graphics: List[Dict[str, Any]],
    scale: float = 2.0
):
    graphics_by_page: Dict[int, List[Dict[str, Any]]] = {}
    for g in graphics:
        graphics_by_page.setdefault(g["page_index"], []).append(g)

    for page_index, page_graphics in graphics_by_page.items():
        page = doc.load_page(page_index)

        for g in page_graphics:
            bbox = g.get("bbox")
            if not bbox:
                g["non_text_contrast"] = default_non_text_contrast()
                continue

            adjacent = sample_outside_ring_rgb(page, bbox, scale=scale, ring_px=4)
            if adjacent is None:
                adjacent = [255, 255, 255]
                method = "outside_ring_fallback_white"
            else:
                method = "outside_ring"

            stroke_rgb = g.get("stroke_rgb")
            fill_rgb = g.get("fill_rgb")

            contrast_against_stroke = None
            contrast_against_fill = None

            if stroke_rgb and len(stroke_rgb) >= 3:
                contrast_against_stroke = float(round(contrast_ratio(stroke_rgb, adjacent), 3))

            if fill_rgb and len(fill_rgb) >= 3:
                contrast_against_fill = float(round(contrast_ratio(fill_rgb, adjacent), 3))

            effective_contrast = contrast_against_stroke
            if effective_contrast is None:
                effective_contrast = contrast_against_fill
            elif contrast_against_fill is not None:
                effective_contrast = min(contrast_against_stroke, contrast_against_fill)

            g["non_text_contrast"] = {
                "adjacent_rgb": adjacent,
                "method": method,
                "contrast_against_stroke": contrast_against_stroke,
                "contrast_against_fill": contrast_against_fill,
                "contrast_against_border": None,
                "passes_3_1": effective_contrast >= 3.0 if effective_contrast is not None else None
            }


def annotate_widgets_non_text_contrast(
    doc: fitz.Document,
    widgets: List[Dict[str, Any]],
    scale: float = 2.0
):
    widgets_by_page: Dict[int, List[Dict[str, Any]]] = {}
    for w in widgets:
        widgets_by_page.setdefault(w["page_index"], []).append(w)

    for page_index, page_widgets in widgets_by_page.items():
        page = doc.load_page(page_index)

        for w in page_widgets:
            bbox = w.get("bbox")
            if not bbox:
                w["non_text_contrast"] = default_non_text_contrast()
                continue

            adjacent = sample_outside_ring_rgb(page, bbox, scale=scale, ring_px=4)
            border_rgb = sample_widget_border_rgb(page, bbox, scale=scale, edge_px=2)

            method = "sampled_widget_border_and_outside_ring"

            if adjacent is None:
                adjacent = [255, 255, 255]
                method += "_fallback_adjacent_white"

            contrast_against_border = None
            if border_rgb is not None:
                contrast_against_border = float(round(contrast_ratio(border_rgb, adjacent), 3))

            w["non_text_contrast"] = {
                "adjacent_rgb": adjacent,
                "method": method,
                "contrast_against_stroke": None,
                "contrast_against_fill": None,
                "contrast_against_border": contrast_against_border,
                "border_rgb": border_rgb,
                "passes_3_1": contrast_against_border >= 3.0 if contrast_against_border is not None else None
            }                 


def annotate_resize_risk(
    text_spans: List[Dict[str, Any]],
    graphics: List[Dict[str, Any]],
    widgets: List[Dict[str, Any]],
    pages: List[Dict[str, Any]]
):
    spans_by_page: Dict[int, List[Dict[str, Any]]] = {}
    graphics_by_page: Dict[int, List[Dict[str, Any]]] = {}
    widgets_by_page: Dict[int, List[Dict[str, Any]]] = {}
    page_dims = {
        p["page_index"]: (float(p["width"]), float(p["height"]))
        for p in pages
    }

    for sp in text_spans:
        spans_by_page.setdefault(sp["page_index"], []).append(sp)

    for g in graphics:
        graphics_by_page.setdefault(g["page_index"], []).append(g)

    for w in widgets:
        widgets_by_page.setdefault(w["page_index"], []).append(w)

    for sp in text_spans:
        bbox = sp.get("bbox")
        if not bbox:
            continue

        sem = sp.get("presentation_semantics", {})
        page_index = sp.get("page_index")
        page_width, page_height = page_dims.get(page_index, (0.0, 0.0))

        text_w = bbox_width(bbox)
        text_h = bbox_height(bbox)
        font_size = float(sp.get("font", {}).get("size", 0.0))
        enlarged_bbox = estimate_resized_text_bbox_200(bbox)

        nearby_text_ids = []
        nearby_graphic_ids = []
        nearby_widget_ids = []
        same_line_overlap_ids = []
        clipping_container_ids = []
        paragraph_flow_neighbors = []

        meaningful_graphics = []
        for g in graphics_by_page.get(page_index, []):
            if is_likely_layout_or_decorative_graphic(g, page_width, page_height):
                continue
            meaningful_graphics.append(g)

        # text collisions
        for other in spans_by_page.get(page_index, []):
            if other.get("id") == sp.get("id"):
                continue

            ob = other.get("bbox")
            if not ob:
                continue

            if bbox_intersects(enlarged_bbox, ob):
                nearby_text_ids.append(other.get("id"))

                if is_same_line(sp, other):
                    same_line_overlap_ids.append(other.get("id"))
                elif looks_like_paragraph_continuation(sp, other):
                    paragraph_flow_neighbors.append(other.get("id"))

        # graphic collisions and tight graphic containers
        for g in meaningful_graphics:
            gb = g.get("bbox")
            if not gb:
                continue

            if bbox_intersects(enlarged_bbox, gb):
                nearby_graphic_ids.append(g.get("id"))

            if bbox_contains(gb, bbox, margin=1.0):
                gw = bbox_width(gb)
                gh = bbox_height(gb)

                if gw <= text_w * 1.4 or gh <= text_h * 1.6:
                    if bbox_exceeds_container(enlarged_bbox, gb, margin=1.0):
                        clipping_container_ids.append(g.get("id"))

        # grouped line-box container detection
        line_box_container = find_line_box_container(bbox, meaningful_graphics)
        if line_box_container is not None:
            if bbox_exceeds_container(enlarged_bbox, line_box_container, margin=1.0):
                clipping_container_ids.append(f"line_box_{sp.get('id')}")

        # widget collisions and tight widget containers
        for w in widgets_by_page.get(page_index, []):
            wb = w.get("bbox")
            if not wb:
                continue

            if bbox_intersects(enlarged_bbox, wb):
                nearby_widget_ids.append(w.get("id"))

            if bbox_contains(wb, bbox, margin=1.0):
                ww = bbox_width(wb)
                wh = bbox_height(wb)

                if ww <= text_w * 1.6 or wh <= text_h * 1.8:
                    if bbox_exceeds_container(enlarged_bbox, wb, margin=1.0):
                        clipping_container_ids.append(w.get("id"))

        risk_score = 0

        # page overflow
        if enlarged_bbox[0] < 0 or enlarged_bbox[2] > page_width:
            risk_score += 2
        if enlarged_bbox[1] < 0 or enlarged_bbox[3] > page_height:
            risk_score += 1

        # small text slightly more vulnerable
        if font_size <= 10:
            risk_score += 1

        # strongest evidence
        if clipping_container_ids:
            risk_score += 4

        if same_line_overlap_ids:
            risk_score += min(4, len(same_line_overlap_ids) * 2)

        # moderate evidence
        risk_score += min(2, len(nearby_widget_ids))
        risk_score += min(2, len(nearby_graphic_ids))

        # count only non-paragraph text collisions
        non_paragraph_text_collisions = max(
            0,
            len(nearby_text_ids) - len(paragraph_flow_neighbors) - len(same_line_overlap_ids)
        )
        risk_score += min(2, non_paragraph_text_collisions)

        if sem.get("is_text_in_image_context", False) or sem.get("is_logo_text", False) or sem.get("is_decorative_text", False) :
            continue

        sp["resize_risk"] = {
            "font_size_pt": font_size,
            "is_small_text": font_size <= 10,
            "span_width": text_w,
            "span_height": text_h,
            "estimated_scale_200_bbox": [float(x) for x in enlarged_bbox],
            "has_nearby_text": bool(nearby_text_ids),
            "nearby_text_ids": nearby_text_ids,
            "same_line_overlap_ids": same_line_overlap_ids,
            "paragraph_flow_neighbor_ids": paragraph_flow_neighbors,
            "has_nearby_graphic": bool(nearby_graphic_ids),
            "nearby_graphic_ids": nearby_graphic_ids,
            "has_nearby_widget": bool(nearby_widget_ids),
            "nearby_widget_ids": nearby_widget_ids,
            "clipping_container_ids": clipping_container_ids,
            "would_overlap_on_scale_200": bool(
                nearby_text_ids or nearby_graphic_ids or nearby_widget_ids or clipping_container_ids
            ),
            "risk_score": risk_score,
        }
