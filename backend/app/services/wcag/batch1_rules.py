from .issue import make_issue
from typing import Dict,List,Any
from backend.app.services.helper_function_b1 import (graphic_overlaps_widget,is_likely_layout_or_decorative_graphic,
                                                    combine_nearby_spans, matching_widget_for_acrofield,normalize_label,collect_label,detect_link_color_only,detect_explicit_color_only_instructions,detect_required_field_color_only,detect_repeated_identical_marker_or_label_color_only)
    



##########
# WCAG 1.4
##########

# 1.4.1 
def rule_1_4_1(document: dict) -> list[dict]:
    issues: list[dict] = []
    issues.extend(detect_link_color_only(document))
    issues.extend(detect_explicit_color_only_instructions(document))
    issues.extend(detect_required_field_color_only(document))
    issues.extend(detect_repeated_identical_marker_or_label_color_only(document))
    return issues

# 1.4.3
def rule_1_4_3(document: dict) -> list[dict]:
    doc=document.get('document',document)
    issues: list[dict] = []
    for text_span in doc.get("text_spans", []):
        sem = text_span.get("presentation_semantics", {})
        skip = (
            sem.get("is_logo_text", False)
            or sem.get("is_decorative_text", False)
            or sem.get("ui_state") == "inactive"
        )

        if not skip:
            contrast = text_span.get("contrast", {})
            is_large = contrast.get("large_text_assumed", False)

            if is_large:
                passed = contrast.get("passes_3_1_large", True)
                recommendation = "Increase the contrast between the text and its background so the contrast ratio is at least 3:1 for large text."
            else:
                passed = contrast.get("passes_4_5_1", True)
                recommendation = "Increase the contrast between the text and its background so the contrast ratio is at least 4.5:1."

            if not passed:
               
                   issues.append( make_issue(
                        criterion="1.4.3",
                        issue="insufficient_text_contrast",
                        location={
                            "page": text_span.get("page_index"),
                            "span_id": text_span.get("id"),
                            "contrast_ratio": contrast.get("ratio"),
                        },
                        severity="high",
                        recommendation=recommendation,
                    ))
                   
    return issues  
 
#1.4.11
def rule_1_4_11(document: dict) -> list[dict]:
    issues: list[dict] = []
    doc = document.get("document", document)

    graphics = doc.get("graphics", [])
    widgets = doc.get("widgets", [])
    pages = doc.get("pages", [])

    page_dims = {
        p["page_index"]: (float(p.get("width", 0.0)), float(p.get("height", 0.0)))
        for p in pages
    }

    for graphic in graphics:
        page_index = graphic.get("page_index")
        page_width, page_height = page_dims.get(page_index, (0.0, 0.0))

        # skip graphics that are really widget visuals
        if graphic_overlaps_widget(graphic, widgets, margin=1.0):
            continue

        # skip layout/decorative shapes
        if is_likely_layout_or_decorative_graphic(graphic, page_width, page_height):
            continue

        ntc = graphic.get("non_text_contrast", {})
        passed = ntc.get("passes_3_1")

        if passed is False:
            issues.append(
                make_issue(
                    criterion="1.4.11",
                    issue="insufficient_non_text_contrast_graphic",
                    location={
                        "page": page_index,
                        "graphic_id": graphic.get("id"),
                    },
                    severity="high",
                    recommendation="Ensure this graphical object has a contrast ratio of at least 3:1 against adjacent colors.",
                )
            )

    for widget in widgets:
        if widget.get("ui_state") == "inactive":
            continue

        ntc = widget.get("non_text_contrast", {})
        passed = ntc.get("passes_3_1")

        if passed is False:
            issues.append(
                make_issue(
                    criterion="1.4.11",
                    issue="insufficient_non_text_contrast_ui_component",
                    location={
                        "page": widget.get("page_index"),
                        "widget_id": widget.get("id"),
                    },
                    severity="high",
                    recommendation="Ensure this user interface component has a contrast ratio of at least 3:1 against adjacent colors.",
                )
            )

    return issues 

# 1.4.4
def rule_1_4_4(document: dict) -> list[dict]:
    issues: list[dict] = []
    doc = document.get("document", document)

    text_spans = doc.get("text_spans", [])
    reported_ids = set()

    spans_by_page: dict[int, list[dict]] = {}
    for sp in text_spans:
        p = sp.get("page_index")
        if p is not None:
            spans_by_page.setdefault(p, []).append(sp)

    def _looks_like_paragraph_flow_here(target: dict, page_spans: list[dict]) -> bool:
        tb = target.get("bbox")
        if not tb:
            return False

        tx0, ty0, tx1, ty1 = tb
        tw = max(0.0, tx1 - tx0)

        for other in page_spans:
            if other.get("id") == target.get("id"):
                continue

            ob = other.get("bbox")
            if not ob:
                continue

            ox0, oy0, ox1, oy1 = ob
            ow = max(0.0, ox1 - ox0)

            if other.get("page_index") != target.get("page_index"):
                continue

            cty = (ty0 + ty1) / 2.0
            coy = (oy0 + oy1) / 2.0
            if abs(cty - coy) <= 8.0:
                continue

            v_gap = max(ty0 - oy1, oy0 - ty1, 0.0)
            if v_gap > 12.0:
                continue

            overlap = max(0.0, min(tx1, ox1) - max(tx0, ox0))
            min_w = min(tw, ow)
            overlap_ratio = (overlap / min_w) if min_w > 0 else 0.0
            left_aligned = abs(tx0 - ox0) <= 25.0

            if overlap_ratio >= 0.4 or left_aligned:
                return True

        return False

    for sp in text_spans:
        span_id = sp.get("id")
        if not span_id or span_id in reported_ids:
            continue

        sem = sp.get("presentation_semantics", {})
        rr = sp.get("resize_risk", {})
        text = (sp.get("text") or "").strip()
        page_index = sp.get("page_index")

        if not text:
            continue

        # Skip weaker targets / exceptions
        if sem.get("is_text_in_image_context", False):
            continue
        if sem.get("is_logo_text", False):
            continue
        if sem.get("is_decorative_text", False):
            continue

        risk_score = rr.get("risk_score", 0)
        same_line_overlap_ids = rr.get("same_line_overlap_ids", [])
        clipping_container_ids = rr.get("clipping_container_ids", [])
        nearby_widget_ids = rr.get("nearby_widget_ids", [])
        nearby_graphic_ids = rr.get("nearby_graphic_ids", [])
        paragraph_flow_neighbor_ids = rr.get("paragraph_flow_neighbor_ids", [])

        paragraph_like = bool(paragraph_flow_neighbor_ids) or _looks_like_paragraph_flow_here(
            sp, spans_by_page.get(page_index, [])
        )

        # Suppress ordinary paragraph wrapping
        if (
            paragraph_like
            and not same_line_overlap_ids
            and not clipping_container_ids
            and not nearby_widget_ids
        ):
            continue

        # Suppress likely short decorative banner text
        if (
            nearby_graphic_ids
            and not same_line_overlap_ids
            and not clipping_container_ids
            and not nearby_widget_ids
            and risk_score <= 2
            and len(text.split()) <= 4
        ):
            continue

        likely_failure = (
            risk_score >= 4
            or bool(clipping_container_ids)
            or len(same_line_overlap_ids) >= 1
            or len(nearby_widget_ids) >= 1
        )

        if not likely_failure:
            continue

        issues.append(
            make_issue(
                criterion="1.4.4",
                issue="text_may_not_resize_to_200_percent_without_loss",
                location={
                    "page": page_index,
                    "span_id": span_id,
                    "text": text,
                },
                severity="low" if risk_score < 7 else "meduim",
                recommendation=(
                    "This text may not resize to 200% without overlapping nearby content or exceeding its available space. "
                    "Review fixed containers, crowded labels, and nearby interactive elements."
                ),
            )
        )
        reported_ids.add(span_id)

    return issues


#############
# WCAG 1.2
#############

#1.2.1
def rule_1_2_1(document: dict) -> list[dict]:
    issues: list[dict] = []
    doc = document.get("document", document)
    media = doc.get("media", {}).get("occurrences", [])

    for m in media:
        media_class = m.get("media_class", "unknown")

        if media_class not in {"audio_only", "video_only"}:
            continue

        has_alt = (
            m.get("has_detectable_transcript", False)
            or m.get("has_detectable_media_alternative", False)
        )

        if has_alt:
            issues.append(
                make_issue(
                    criterion="1.2.1",
                    issue="audio_or_video_only_alternative_detected",
                    location={
                        "page": m.get("page_index"),
                        "media_id": m.get("id"),
                        "media_class": media_class,
                    },
                    severity="pass",
                    recommendation="A likely media alternative was detected."
                )
            )
            continue

        issues.append(
            make_issue(
                criterion="1.2.1",
                issue="audio_or_video_only_alternative_needs_review",
                location={
                    "page": m.get("page_index"),
                    "media_id": m.get("id"),
                    "media_class": media_class,
                },
                severity="needs_review",
                recommendation=(
                    "This PDF appears to contain prerecorded audio-only or video-only media, "
                    "but an equivalent media alternative could not be verified automatically."
                ),
            )
        )

    return issues

# 1.2.2
def rule_1_2_2(document: dict) -> list[dict]:
    issues: list[dict] = []
    doc = document.get("document", document)
    media = doc.get("media", {}).get("occurrences", [])

    for m in media:
        if m.get("media_class") != "audio_video":
            continue
        if m.get("looks_live", False):
            continue

        if m.get("has_detectable_captions", False):
            issues.append(
                make_issue(
                    criterion="1.2.2",
                    issue="prerecorded_captions_detected",
                    location={
                        "page": m.get("page_index"),
                        "media_id": m.get("id"),
                    },
                    severity="pass",
                    recommendation="Caption evidence was detected."
                )
            )
            continue

        issues.append(
            make_issue(
                criterion="1.2.2",
                issue="prerecorded_captions_need_review",
                location={
                    "page": m.get("page_index"),
                    "media_id": m.get("id"),
                },
                severity="needs_review",
                recommendation=(
                    "This PDF appears to contain prerecorded synchronized media, "
                    "but captions could not be verified automatically."
                ),
            )
        )

    return issues

#1.2.3
def rule_1_2_3(document: dict) -> list[dict]:
    issues: list[dict] = []
    doc = document.get("document", document)
    media = doc.get("media", {}).get("occurrences", [])

    for m in media:
        if m.get("media_class") != "audio_video":
            continue

        if m.get("looks_live",False):
           continue

        has_alt = (
            m.get("has_detectable_audio_description", False)
            or m.get("has_detectable_media_alternative", False)
            or m.get("has_detectable_transcript", False)
        )

        if has_alt:
            issues.append(
                make_issue(
                    criterion="1.2.3",
                    issue="audio_description_or_media_alternative_detected",
                    location={
                        "page": m.get("page_index"),
                        "media_id": m.get("id"),
                    },
                    severity="pass",
                    recommendation="A likely audio description or media alternative was detected."
                )
            )
            continue

        issues.append(
            make_issue(
                criterion="1.2.3",
                issue="audio_description_or_media_alternative_needs_review",
                location={
                    "page": m.get("page_index"),
                    "media_id": m.get("id"),
                },
                severity="needs_review",
                recommendation=(
                    "This PDF appears to contain prerecorded synchronized media, "
                    "but audio description or a media alternative could not be verified automatically."
                ),
            )
        )

    return issues

#1.2.4
def rule_1_2_4(document: dict) -> list[dict]:
    issues: list[dict] = []
    doc = document.get("document", document)
    media = doc.get("media", {}).get("occurrences", [])

    for m in media:
        if m.get("media_class") != "audio_video":
            continue
        if not m.get("looks_live", False):
            continue

        if m.get("has_detectable_captions", False):
            issues.append(
                make_issue(
                    criterion="1.2.4",
                    issue="live_captions_detected",
                    location={
                        "page": m.get("page_index"),
                        "media_id": m.get("id"),
                    },
                    severity="pass",
                    recommendation="Caption evidence for live media was detected."
                )
            )
            continue

        issues.append(
            make_issue(
                criterion="1.2.4",
                issue="live_captions_need_review",
                location={
                    "page": m.get("page_index"),
                    "media_id": m.get("id"),
                },
                severity="needs_review",
                recommendation=(
                    "This PDF appears to reference live synchronized media, "
                    "but captions could not be verified automatically."
                ),
            )
        )

    return issues

#1.2.5
def rule_1_2_5(document: dict) -> list[dict]:
    issues: list[dict] = []
    doc = document.get("document", document)
    media = doc.get("media", {}).get("occurrences", [])

    for m in media:
        if m.get("media_class") != "audio_video":
            continue
        if m.get("looks_live", False):
            continue

        if m.get("has_detectable_audio_description", False):
            issues.append(
                make_issue(
                    criterion="1.2.5",
                    issue="audio_description_detected",
                    location={
                        "page": m.get("page_index"),
                        "media_id": m.get("id"),
                    },
                    severity="pass",
                    recommendation="Audio description evidence was detected."
                )
            )
            continue

        issues.append(
            make_issue(
                criterion="1.2.5",
                issue="audio_description_needs_review",
                location={
                    "page": m.get("page_index"),
                    "media_id": m.get("id"),
                },
                severity="needs_review",
                recommendation=(
                    "This PDF appears to contain prerecorded video content, "
                    "but audio description could not be verified automatically."
                ),
            )
        )

    return issues

#########
#wcag 2.5
#########

# 2.5.3
def rule_2_5_3(document: dict) -> list[dict]:
    issues: list[dict] = []
    doc = document.get("document", document)

    interactivity = doc.get("interactivity", {})
    acroform_fields = interactivity.get("acroform_fields", [])
    widgets = doc.get("widgets", [])
    text_spans = doc.get("text_spans", [])

    spans_by_page: Dict[int, List[Dict[str, Any]]] = {}
    for sp in text_spans:
        p = sp.get("page_index")
        if p is not None:
            spans_by_page.setdefault(p, []).append(sp)

    for field in acroform_fields:
        
        widget = matching_widget_for_acrofield(field, widgets)
        if not widget:
            continue
        page_index = field.get("page_index")
        if page_index is None:
                 page_index = widget.get("page_index")

        if page_index is None:
            continue

        page_spans = spans_by_page.get(page_index, [])
        label_spans = collect_label(widget, page_spans)
        visible_label = combine_nearby_spans(label_spans)

        if not visible_label:
            continue

        programmatic_name = field.get("tooltip") or field.get("name") or ""

        norm_visible = normalize_label(visible_label)
        norm_programmatic = normalize_label(programmatic_name)

        if not norm_visible:
            continue

        if not norm_programmatic:
            issues.append(
                make_issue(
                    criterion="2.5.3",
                    issue="label_in_name_needs_review",
                    location={
                        "page": page_index,
                        "field_id": field.get("id"),
                        "widget_id": widget.get("id"),
                        "visible_label": visible_label,
                    },
                    severity="needs_review",
                    recommendation=(
                        "This control has visible label text, but a matching programmatic name "
                        "could not be confirmed. Ensure the accessible name contains the visible label."
                    ),
                )
            )
            continue

        if norm_visible in norm_programmatic:
            continue
        visible_words = [w for w in norm_visible.split() if len(w) >= 3]

        # very short label
        if not visible_words:
            issues.append(
                make_issue(
                    criterion="2.5.3",
                    issue="label_in_name_needs_review",
                    location={
                        "page": page_index,
                        "field_id": field.get("id"),
                        "widget_id": widget.get("id"),
                        "visible_label": visible_label,
                        "programmatic_name": programmatic_name,
                    },
                    severity="needs_review",
                    recommendation=(
                        "This control has a very short visible label, so the label-in-name "
                        "relationship could not be confirmed automatically."
                    ),
                )
            )
            continue

        if not any(word in norm_programmatic for word in visible_words):
            issues.append(
                make_issue(
                    criterion="2.5.3",
                    issue="label_not_in_name",
                    location={
                        "page": page_index,
                        "field_id": field.get("id"),
                        "widget_id": widget.get("id"),
                        "visible_label": visible_label,
                        "programmatic_name": programmatic_name,
                    },
                    severity="high",
                    recommendation=(
                        "Ensure the accessible name contains the same text as the visible label."
                    ),
                )
            )

    return issues

#2.5.1
def rule_2_5_1(document: dict) -> list[dict]:
    issues: list[dict] = []
    doc = document.get("document", document)

    interactivity = doc.get("interactivity", {})
    media_occurrences = doc.get("media", {}).get("occurrences", [])

    has_javascript = interactivity.get("has_javascript", False)
    javascript_triggers = interactivity.get("javascript_triggers", [])
    acroform_fields = interactivity.get("acroform_fields", [])

    has_richmedia = any(
        (m.get("source") in {"RichMedia", "Screen"} or m.get("annotation_subtype") in {"RichMedia", "Screen"})
        for m in media_occurrences
    )

    has_signature_field = any(
        f.get("type") == "Sig"
        for f in acroform_fields
    )

    if not has_javascript and not has_richmedia and not has_signature_field:
        return issues

    issues.append(
        make_issue(
            criterion="2.5.1",
            issue="pointer_gesture_needs_review",
            location={
                "javascript_triggers": javascript_triggers,
                "has_richmedia": has_richmedia,
                "has_signature_field": has_signature_field,
            },
            severity="needs_review",
            recommendation=(
                "This PDF contains interactive or scripted content that may involve gesture-based input. "
                "Verify that any multipoint or path-based gesture can also be performed with a single pointer "
                "without requiring a path-based gesture, unless essential."
            ),
        )
    )

    return issues

# 2.5.2
def rule_2_5_2(document: dict) -> list[dict]:
    issues: list[dict] = []
    doc = document.get("document", document)

    interactivity = doc.get("interactivity", {})
    acroform_fields = interactivity.get("acroform_fields", [])
    javascript_triggers = interactivity.get("javascript_triggers", [])
    has_javascript = interactivity.get("has_javascript", False)
    submit_actions = interactivity.get("submit_actions", [])
    has_submit_action = interactivity.get("has_submit_action", False)

    risky_fields = []

    for field in acroform_fields:
        validation_actions = field.get("validation_actions", {})
        if not validation_actions:
            continue

        has_field_js = any(
            action_info.get("has_javascript", False)
            for action_info in validation_actions.values()
            if isinstance(action_info, dict)
        )

        if has_field_js:
            risky_fields.append({
                "field_id": field.get("id"),
                "page_index": field.get("page_index"),
                "name": field.get("name"),
                "type": field.get("type"),
            })

    if not has_javascript and not has_submit_action and not risky_fields:
        return issues

    issues.append(
        make_issue(
            criterion="2.5.2",
            issue="pointer_cancellation_needs_review",
            location={
                "has_javascript": has_javascript,
                "javascript_triggers": javascript_triggers,
                "submit_actions": submit_actions,
                "field_ids": [f.get("field_id") for f in risky_fields],
            },
            severity="needs_review",
            recommendation=(
                "This PDF contains scripted or submit-like interactive behavior. "
                "Verify that pointer actions are not completed on pointer-down alone, "
                "or that abort, undo, or reversal is available where required."
            ),
        )
    )

    return issues

#2.5.4
def rule_2_5_4(document: dict) -> list[dict]:
    issues: list[dict] = []
    doc = document.get("document", document)

    interactivity = doc.get("interactivity", {})
    media_occurrences = doc.get("media", {}).get("occurrences", [])

    has_javascript = interactivity.get("has_javascript", False)
    javascript_triggers = interactivity.get("javascript_triggers", [])

    has_richmedia = any(
        (m.get("source") in {"RichMedia", "Screen"} or m.get("annotation_subtype") in {"RichMedia", "Screen"})
        for m in media_occurrences
    )

    # motion keywords 
    motion_keywords = {"motion", "tilt", "shake", "orientation", "accelerometer", "gyro", "gyroscope"}
    trigger_text = " ".join(
        str(t.get("trigger", "")) + " " + str(t.get("location", ""))
        for t in javascript_triggers
        if isinstance(t, dict)
    ).lower()

    has_motion_signal = any(k in trigger_text for k in motion_keywords)

    if not has_javascript and not has_richmedia and not has_motion_signal:
        return issues

    issues.append(
        make_issue(
            criterion="2.5.4",
            issue="motion_actuation_needs_review",
            location={
                "has_javascript": has_javascript,
                "javascript_triggers": javascript_triggers,
                "has_richmedia": has_richmedia,
                "has_motion_signal": has_motion_signal,
            },
            severity="needs_review",
            recommendation=(
                "This PDF contains advanced scripted or embedded interactive content. "
                "Verify that any motion-based functionality can also be operated through "
                "user interface components and that motion response can be disabled, unless essential."
            ),
        )
    )

    return issues

############
# WCAG 1.1.1
############





def run_batch2_rules(document: dict) -> list[dict]:
    issues: list[dict] = []
    

    return issues  
