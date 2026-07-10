from typing import Any, Dict

def default_presentation_semantics() -> Dict[str, Any]:
    return {
        "is_logo_text": False,
        "logo_confidence": 0.0,
        "logo_reason": None,

        "is_decorative_text": False,
        "decorative_confidence": 0.0,
        "decorative_reason": None,

        "is_text_in_image_context": False,
        "image_context_confidence": 0.0,
        "image_context_reason": None,
        "overlapping_image_ids": [],

        "is_ui_label": False,
        "ui_component_type": None,
        "ui_state": None
    }

def default_resize_risk() -> Dict[str, Any]:
    return {
        "font_size_pt": 0.0,
        "is_small_text": False,
        "span_width": 0.0,
        "span_height": 0.0,
        "estimated_scale_200_bbox": None,
        "has_nearby_text": False,
        "nearby_text_ids": [],
        "same_line_overlap_ids": [],
        "paragraph_flow_neighbor_ids": [],
        "has_nearby_graphic": False,
        "nearby_graphic_ids": [],
        "has_nearby_widget": False,
        "nearby_widget_ids": [],
        "clipping_container_ids": [],
        "would_overlap_on_scale_200": False,
        "suppressed_due_to_semantics": False,
        "risk_score": 0
    }


def default_non_text_contrast() -> Dict[str, Any]:
    return {
        "adjacent_rgb": None,
        "method": None,
        "contrast_against_stroke": None,
        "contrast_against_fill": None,
        "contrast_against_border": None,
        "passes_3_1": None
    }
