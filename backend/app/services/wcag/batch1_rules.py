from .issue import make_issue
from typing import Dict,List,Any
from .helper_function_b1 import (
    graphic_overlaps_widget,
    is_likely_layout_or_decorative_graphic,
    combine_nearby_spans,
    matching_widget_for_acrofield,
    normalize_label,
    collect_label,
    detect_link_color_only,
    detect_explicit_color_only_instructions,
    detect_required_field_color_only,
    detect_repeated_identical_marker_or_label_color_only,
    _is_descriptive_control_name,_is_suspicious_alt_text
)

############
# WCAG 1.1
############

# 1.1.1
def rule_1_1_1(document: dict) -> list[dict]:
    issues: list[dict] = []
    doc = document.get("document", document)

    images = doc.get("images", {}).get("occurrences", [])
    widgets = doc.get("widgets", [])
    media = doc.get("media", {}).get("occurrences", [])
    interactivity = doc.get("interactivity", {})
    acroform_fields = interactivity.get("acroform_fields", [])

    # Rule-level not applicable
    if not images and not widgets and not media and not acroform_fields:
        issues.append(
            make_issue(
                criterion="1.1.1",
                issue="rule_not_applicable",
                location={},
                severity="not_applicable",
                recommendation="No relevant non-text content, controls, or time-based media were detected."
            )
        )
        return issues

    #images
    for img in images:
        image_id = img.get("id")
        page_index = img.get("page_index")
        alt_text = img.get("alt_text")

        # decorative image
        if alt_text == "":
            issues.append(
                make_issue(
                    criterion="1.1.1",
                    issue="decorative_image_marked_correctly",
                    location={"page": page_index, "image_id": image_id},
                    severity="pass",
                    recommendation="Decorative image is correctly marked so assistive technology can ignore it."
                )
            )
            continue

        # non-empty alt text exists
        if isinstance(alt_text, str) and alt_text.strip():
            if _is_suspicious_alt_text(alt_text):
                issues.append(
                    make_issue(
                        criterion="1.1.1",
                        issue="image_alt_text_not_descriptive",
                        location={
                            "page": page_index,
                            "image_id": image_id,
                        },
                        severity="needs_review",
                        recommendation=(
                            "The alternative text appears generic or non-descriptive. "
                            "Provide a meaningful description that conveys the image's purpose."
                        ),
                    )
                )
            else:
                issues.append(
                    make_issue(
                        criterion="1.1.1",
                        issue="image_text_alternative_detected",
                        location={"page": page_index, "image_id": image_id},
                        severity="pass",
                        recommendation="A text alternative was detected for this image."
                    )
                )
            continue

        # missing alt
        issues.append(
            make_issue(
                criterion="1.1.1",
                issue="image_missing_text_alternative",
                location={
                    "page": page_index,
                    "image_id": image_id,
                },
                severity="high",
                recommendation=(
                    "Provide a text alternative for this image using the Figure tag /Alt entry. "
                    "If the image is decorative, mark it so assistive technology can ignore it."
                ),
            )
        )

    # control input 
    field_by_name = {}
    for f in acroform_fields:
        name = (f.get("name") or "").strip().lower()
        if name:
            field_by_name[name] = f

    for widget in widgets:
        widget_id = widget.get("id")
        page_index = widget.get("page_index")
        field_name = (widget.get("field_name") or "").strip().lower()

        matched_field = field_by_name.get(field_name)
        tooltip = None
        raw_name = None

        if matched_field:
            tooltip = matched_field.get("tooltip")
            raw_name = matched_field.get("name")

        widget_field_name = widget.get("field_name")

        if (
            _is_descriptive_control_name(tooltip)
            or _is_descriptive_control_name(raw_name)
            or _is_descriptive_control_name(widget_field_name)
        ):
            issues.append(
                make_issue(
                    criterion="1.1.1",
                    issue="control_name_detected",
                    location={"page": page_index, "widget_id": widget_id},
                    severity="pass",
                    recommendation="A descriptive accessible name was detected for this control."
                )
            )
            continue

        issues.append(
            make_issue(
                criterion="1.1.1",
                issue="control_missing_name",
                location={
                    "page": page_index,
                    "widget_id": widget_id,
                },
                severity="high",
                recommendation=(
                    "Provide an accessible name for this control or input. "
                    "Use a meaningful tooltip (/TU) or another descriptive programmatic name that explains its purpose."
                ),
            )
        )

    #time based media
    for m in media:
        media_id = m.get("id")
        page_index = m.get("page_index")
        media_class = m.get("media_class", "unknown")

        strong_identification = (
            m.get("has_detectable_transcript", False)
            or m.get("has_detectable_media_alternative", False)
            or m.get("has_detectable_captions", False)
            or m.get("has_detectable_audio_description", False)
        )

        weak_identification = (
            bool(m.get("nearby_text_ids"))
            or bool(m.get("filename"))
        )

        if strong_identification:
            issues.append(
                make_issue(
                    criterion="1.1.1",
                    issue="time_based_media_descriptive_identification_detected",
                    location={
                        "page": page_index,
                        "media_id": media_id,
                        "media_class": media_class,
                    },
                    severity="pass",
                    recommendation="A transcript, captions, audio description, or other strong alternative evidence was detected for this media."
                )
            )
            continue

        if weak_identification:
            issues.append(
                make_issue(
                    criterion="1.1.1",
                    issue="time_based_media_identification_uncertain",
                    location={
                        "page": page_index,
                        "media_id": media_id,
                        "media_class": media_class,
                    },
                    severity="needs_review",
                    recommendation=(
                        "Some contextual evidence for this media was detected (such as nearby text or filename), "
                        "but it is unclear whether an appropriate text alternative exists."
                    ),
                )
            )
            continue

        issues.append(
            make_issue(
                criterion="1.1.1",
                issue="time_based_media_missing_descriptive_identification",
                location={
                    "page": page_index,
                    "media_id": media_id,
                    "media_class": media_class,
                },
                severity="needs_review",
                recommendation=(
                    "Provide descriptive identification for this time-based media. "
                    "Ensure appropriate alternatives are evaluated under WCAG 1.2.x."
                ),
            )
        )

    return issues

##########
# WCAG 1.4
##########

# 1.4.1 
def rule_1_4_1(document: dict) -> list[dict]:
    issues: list[dict] = []
    doc = document.get("document", document)
    text_spans = doc.get("text_spans", [])
    text_blocks = doc.get("text_blocks", [])
    links = doc.get("links", [])
    widgets = doc.get("widgets", [])

    if not links and not text_blocks and not text_spans and not widgets:
        issues.append(
            make_issue(
                criterion="1.4.1",
                issue="use_of_color_not_applicable",
                location={},
                severity="not_applicable",
                recommendation="No content where color could convey meaning was detected."
            )
        )
    issues.extend(detect_link_color_only(document))
    issues.extend(detect_explicit_color_only_instructions(document))
    issues.extend(detect_required_field_color_only(document))
    issues.extend(detect_repeated_identical_marker_or_label_color_only(document))    

    return issues


# 1.4.3
def rule_1_4_3(document: dict) -> list[dict]:
    issues: list[dict] = []
    doc = document.get("document", document)

    text_spans = doc.get("text_spans", [])

    relevant_spans = []

    for text_span in text_spans:
        sem = text_span.get("presentation_semantics", {})
        skip = (
            sem.get("is_logo_text", False)
            or sem.get("is_decorative_text", False)
            or sem.get("ui_state") == "inactive"
        )

        text = (text_span.get("text") or "").strip()
        if not text or skip:
            continue

        relevant_spans.append(text_span)
    if not relevant_spans:
        issues.append(
            make_issue(
                criterion="1.4.3",
                issue="rule_not_applicable",
                location={},
                severity="not_applicable",
                recommendation="No relevant visible text requiring contrast evaluation was detected."
            )
        )
        return issues

    for text_span in relevant_spans:
        contrast = text_span.get("contrast", {})
        page_index = text_span.get("page_index")
        span_id = text_span.get("id")
        ratio = contrast.get("ratio")
        is_large = contrast.get("large_text_assumed", False)

        if is_large:
            passed = contrast.get("passes_3_1_large")
            recommendation = (
                "Increase the contrast between the text and its background "
                "so the contrast ratio is at least 3:1 for large text."
            )
        else:
            passed = contrast.get("passes_4_5_1")
            recommendation = (
                "Increase the contrast between the text and its background "
                "so the contrast ratio is at least 4.5:1."
            )

        if passed is None or ratio is None:
            issues.append(
                make_issue(
                    criterion="1.4.3",
                    issue="text_contrast_needs_review",
                    location={
                        "page": page_index,
                        "span_id": span_id,
                    },
                    severity="needs_review",
                    recommendation=(
                        "The text contrast could not be determined reliably automatically. "
                        "Manual review is recommended."
                    ),
                )
            )
            continue

        if passed is True:
            issues.append(
                make_issue(
                    criterion="1.4.3",
                    issue="text_contrast_sufficient",
                    location={
                        "page": page_index,
                        "span_id": span_id,
                        "contrast_ratio": ratio,
                    },
                    severity="pass",
                    recommendation="This text appears to meet the required contrast ratio."
                )
            )
            continue

        issues.append(
            make_issue(
                criterion="1.4.3",
                issue="insufficient_text_contrast",
                location={
                    "page": page_index,
                    "span_id": span_id,
                    "contrast_ratio": ratio,
                },
                severity="high",
                recommendation=recommendation,
            )
        )

    return issues

 
#1.4.11
def rule_1_4_11(document: dict) -> list[dict]:
    issues: list[dict] = []
    doc = document.get("document", document)

    graphics = doc.get("graphics", [])
    widgets = doc.get("widgets", [])
    pages = doc.get("pages", [])

    if not graphics and not widgets:
        issues.append(
            make_issue(
                criterion="1.4.11",
                issue="rule_not_applicable",
                location={},
                severity="not_applicable",
                recommendation="No graphical objects or UI components were detected."
            )
        )
        return issues

    page_dims = {
        p["page_index"]: (float(p.get("width", 0.0)), float(p.get("height", 0.0)))
        for p in pages
    }


    for graphic in graphics:
        page_index = graphic.get("page_index")
        page_width, page_height = page_dims.get(page_index, (0.0, 0.0))

        # skip
        if graphic_overlaps_widget(graphic, widgets, margin=1.0):
            continue

        # skip 
        if is_likely_layout_or_decorative_graphic(graphic, page_width, page_height):
            continue

        ntc = graphic.get("non_text_contrast", {})
        passed = ntc.get("passes_3_1")

        if passed is True:
            issues.append(
                make_issue(
                    criterion="1.4.11",
                    issue="graphic_non_text_contrast_sufficient",
                    location={
                        "page": page_index,
                        "graphic_id": graphic.get("id"),
                    },
                    severity="pass",
                    recommendation="This graphical object appears to meet the 3:1 non-text contrast requirement."
                )
            )
            continue

        # fail
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
            continue

        issues.append(
            make_issue(
                criterion="1.4.11",
                issue="graphic_non_text_contrast_uncertain",
                location={
                    "page": page_index,
                    "graphic_id": graphic.get("id"),
                },
                severity="needs_review",
                recommendation="Unable to determine non-text contrast automatically. Manual review is recommended.",
            )
        )

    for widget in widgets:

        if widget.get("ui_state") == "inactive":
            continue

        ntc = widget.get("non_text_contrast", {})
        passed = ntc.get("passes_3_1")

        if passed is True:
            issues.append(
                make_issue(
                    criterion="1.4.11",
                    issue="ui_component_non_text_contrast_sufficient",
                    location={
                        "page": widget.get("page_index"),
                        "widget_id": widget.get("id"),
                    },
                    severity="pass",
                    recommendation="This user interface component appears to meet the 3:1 non-text contrast requirement.",
                )
            )
            continue

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
            continue
        issues.append(
            make_issue(
                criterion="1.4.11",
                issue="ui_component_non_text_contrast_uncertain",
                location={
                    "page": widget.get("page_index"),
                    "widget_id": widget.get("id"),
                },
                severity="needs_review",
                recommendation="Unable to determine non-text contrast automatically. Manual review is recommended.",
            )
        )

    return issues
# 1.4.4
def rule_1_4_4(document: dict) -> list[dict]:
    issues: list[dict] = []
    doc = document.get("document", document)

    text_spans = doc.get("text_spans", [])
    reported_ids = set()

    if not text_spans:
        issues.append(
            make_issue(
                criterion="1.4.4",
                issue="rule_not_applicable",
                location={},
                severity="not_applicable",
                recommendation="No text content was detected in the document."
            )
        )
        return issues

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

        # Skip exceptions
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

        # Suppress short decorative banner text
        if (
            nearby_graphic_ids
            and not same_line_overlap_ids
            and not clipping_container_ids
            and not nearby_widget_ids
            and risk_score <= 2
            and len(text.split()) <= 4
        ):
            continue

        if (
            risk_score <= 1
            and not same_line_overlap_ids
            and not clipping_container_ids
            and not nearby_widget_ids
            and not nearby_graphic_ids
        ):
            issues.append(
                make_issue(
                    criterion="1.4.4",
                    issue="text_resize_likely_safe",
                    location={
                        "page": page_index,
                        "span_id": span_id,
                        "text": text,
                    },
                    severity="pass",
                    recommendation="This text appears likely to resize to 200% without layout conflicts."
                )
            )
            continue

        if (
            risk_score >= 2
            and not same_line_overlap_ids
            and not clipping_container_ids
            and not nearby_widget_ids
        ):
            issues.append(
                make_issue(
                    criterion="1.4.4",
                    issue="text_resize_uncertain",
                    location={
                        "page": page_index,
                        "span_id": span_id,
                        "text": text,
                    },
                    severity="needs_review",
                    recommendation=(
                        "Text resizing behaviour is uncertain. Verify that the text can be resized to 200% "
                        "without overlap or clipping."
                    ),
                )
            )
            continue

        likely_failure = (
            risk_score >= 4
            or bool(clipping_container_ids)
            or len(same_line_overlap_ids) >= 1
            or len(nearby_widget_ids) >= 1
        )

        if not likely_failure:
            continue

        # Severity scaling
        if risk_score >= 7 or clipping_container_ids:
            severity = "high"
        elif risk_score >= 5:
            severity = "medium"
        else:
            severity = "low"

        issues.append(
            make_issue(
                criterion="1.4.4",
                issue="text_may_not_resize_to_200_percent_without_loss",
                location={
                    "page": page_index,
                    "span_id": span_id,
                },
                severity=severity,
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

    applicable_media = [
        m for m in media
        if m.get("media_class") in {"audio_only", "video_only"}
    ]

    if not applicable_media:
        issues.append(
            make_issue(
                criterion="1.2.1",
                issue="audio_or_video_only_not_applicable",
                location={},
                severity="not_applicable",
                recommendation="No prerecorded audio-only or video-only media was detected."
            )
        )
        return issues

    for m in applicable_media:
        media_class = m.get("media_class", "unknown")

        strong_alt = (
            m.get("has_detectable_transcript", False)
            or m.get("has_detectable_media_alternative", False)
        )

        weak_alt = (
            bool(m.get("nearby_text_ids"))
            or bool(m.get("filename"))
        )

        if strong_alt:
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

        if weak_alt:
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
                    recommendation="Possible alternative-related evidence was found, but equivalence could not be confirmed."
                )
            )
            continue

        issues.append(
            make_issue(
                criterion="1.2.1",
                issue="audio_or_video_only_alternative_missing",
                location={
                    "page": m.get("page_index"),
                    "media_id": m.get("id"),
                    "media_class": media_class,
                },
                severity="high",
                recommendation="Provide an equivalent transcript or media alternative for this prerecorded audio-only or video-only media."
            )
        )

    return issues

# 1.2.2
def rule_1_2_2(document: dict) -> list[dict]:
    issues: list[dict] = []
    doc = document.get("document", document)
    media = doc.get("media", {}).get("occurrences", [])

    applicable_media = [
        m for m in media
        if m.get("media_class") == "audio_video" and not m.get("looks_live", False)
    ]

    if not applicable_media:
        issues.append(
            make_issue(
                criterion="1.2.2",
                issue="prerecorded_captions_not_applicable",
                location={},
                severity="not_applicable",
                recommendation="No prerecorded synchronized media was detected."
            )
        )
        return issues

    for m in applicable_media:

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

        weak_caption_evidence = (
            bool(m.get("nearby_text_ids"))
            or bool(m.get("filename"))
        )

        if weak_caption_evidence:
            issues.append(
                make_issue(
                    criterion="1.2.2",
                    issue="prerecorded_captions_need_review",
                    location={
                        "page": m.get("page_index"),
                        "media_id": m.get("id"),
                    },
                    severity="needs_review",
                    recommendation="Possible caption-related text was found but captions could not be confirmed."
                )
            )
            continue

        issues.append(
            make_issue(
                criterion="1.2.2",
                issue="prerecorded_captions_missing",
                location={
                    "page": m.get("page_index"),
                    "media_id": m.get("id"),
                },
                severity="high",
                recommendation="Provide captions for this prerecorded synchronized media."
            )
        )

    return issues


#1.2.3
def rule_1_2_3(document: dict) -> list[dict]:
    issues: list[dict] = []
    doc = document.get("document", document)
    media = doc.get("media", {}).get("occurrences", [])

    applicable_media = [
        m for m in media
        if m.get("media_class") == "audio_video" and not m.get("looks_live", False)
    ]

    if not applicable_media:
        issues.append(
            make_issue(
                criterion="1.2.3",
                issue="audio_description_or_media_alternative_not_applicable",
                location={},
                severity="not_applicable",
                recommendation="No prerecorded synchronized media was detected."
            )
        )
        return issues

    for m in applicable_media:
        strong_alt = (
            m.get("has_detectable_audio_description", False)
            or m.get("has_detectable_media_alternative", False)
            or m.get("has_detectable_transcript", False)
        )

        weak_alt = (
            bool(m.get("nearby_text_ids"))
            or bool(m.get("filename"))
        )

        if strong_alt:
            issues.append(
                make_issue(
                    criterion="1.2.3",
                    issue="audio_description_or_media_alternative_detected",
                    location={
                        "page": m.get("page_index"),
                        "media_id": m.get("id"),
                    },
                    severity="pass",
                    recommendation="Audio description or a media alternative was detected."
                )
            )
            continue

        if weak_alt:
            issues.append(
                make_issue(
                    criterion="1.2.3",
                    issue="audio_description_or_media_alternative_needs_review",
                    location={
                        "page": m.get("page_index"),
                        "media_id": m.get("id"),
                    },
                    severity="needs_review",
                    recommendation="Possible alternative-related evidence was found but could not be confirmed."
                )
            )
            continue

        issues.append(
            make_issue(
                criterion="1.2.3",
                issue="audio_description_or_media_alternative_missing",
                location={
                    "page": m.get("page_index"),
                    "media_id": m.get("id"),
                },
                severity="high",
                recommendation="Provide audio description or a media alternative for this prerecorded synchronized media."
            )
        )

    return issues

#1.2.4
def rule_1_2_4(document: dict) -> list[dict]:
    issues: list[dict] = []
    doc = document.get("document", document)
    media = doc.get("media", {}).get("occurrences", [])

    applicable_media = [
        m for m in media
        if m.get("media_class") == "audio_video" and m.get("looks_live", False)
    ]

    if not applicable_media:
        issues.append(
            make_issue(
                criterion="1.2.4",
                issue="live_captions_not_applicable",
                location={},
                severity="not_applicable",
                recommendation="No live synchronized media was detected."
            )
        )
        return issues

    for m in applicable_media:
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

        weak_caption_evidence = (
            bool(m.get("nearby_text_ids"))
            or bool(m.get("filename"))
        )

        if weak_caption_evidence:
            issues.append(
                make_issue(
                    criterion="1.2.4",
                    issue="live_captions_need_review",
                    location={
                        "page": m.get("page_index"),
                        "media_id": m.get("id"),
                    },
                    severity="needs_review",
                    recommendation="Possible caption-related evidence was detected, but the presence of captions for this live synchronized media could not be confirmed automatically. Verify that captions are provided for the live media."
                )
            )
            continue

        issues.append(
            make_issue(
                criterion="1.2.4",
                issue="live_captions_missing",
                location={
                    "page": m.get("page_index"),
                    "media_id": m.get("id"),
                },
                severity="high",
                recommendation="Provide captions for this live synchronized media."
            )
        )

    return issues

#1.2.5
def rule_1_2_5(document: dict) -> list[dict]:
    issues: list[dict] = []
    doc = document.get("document", document)
    media = doc.get("media", {}).get("occurrences", [])

    applicable_media = [
        m for m in media
        if m.get("media_class") == "audio_video" and not m.get("looks_live", False)
    ]

    if not applicable_media:
        issues.append(
            make_issue(
                criterion="1.2.5",
                issue="audio_description_not_applicable",
                location={},
                severity="not_applicable",
                recommendation="No prerecorded synchronized media was detected."
            )
        )
        return issues

    for m in applicable_media:
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

        weak_audio_desc_evidence = (
            bool(m.get("nearby_text_ids"))
            or bool(m.get("filename"))
        )

        if weak_audio_desc_evidence:
            issues.append(
                make_issue(
                    criterion="1.2.5",
                    issue="audio_description_needs_review",
                    location={
                        "page": m.get("page_index"),
                        "media_id": m.get("id"),
                    },
                    severity="needs_review",
                    recommendation="Possible audio-description-related evidence was found but could not be confirmed."
                )
            )
            continue

        issues.append(
            make_issue(
                criterion="1.2.5",
                issue="audio_description_missing",
                location={
                    "page": m.get("page_index"),
                    "media_id": m.get("id"),
                },
                severity="high",
                recommendation="Provide audio description for this prerecorded video content."
            )
        )

    return issues


#########
#wcag 2.5
#########

#2.5.3
def rule_2_5_3(document: dict) -> list[dict]:
    issues: list[dict] = []
    doc = document.get("document", document)

    interactivity = doc.get("interactivity", {})
    acroform_fields = interactivity.get("acroform_fields", [])
    widgets = doc.get("widgets", [])
    text_spans = doc.get("text_spans", [])

    if not acroform_fields or not widgets:
        issues.append(
            make_issue(
                criterion="2.5.3",
                issue="label_in_name_not_applicable",
                location={},
                severity="not_applicable",
                recommendation="No applicable form controls with detectable programmatic names and visible labels were found for label-in-name checking."
            )
        )
        return issues

    spans_by_page: Dict[int, List[Dict[str, Any]]] = {}
    for sp in text_spans:
        p = sp.get("page_index")
        if p is not None:
            spans_by_page.setdefault(p, []).append(sp)

    applicable_found = False

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

        applicable_found = True
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
            issues.append(
                make_issue(
                    criterion="2.5.3",
                    issue="label_in_name_detected",
                    location={
                        "page": page_index,
                        "field_id": field.get("id"),
                        "widget_id": widget.get("id"),
                        "visible_label": visible_label,
                        "programmatic_name": programmatic_name,
                    },
                    severity="pass",
                    recommendation=(
                        "This control has visible label text, and the accessible name appears to contain the visible label."
                    ),
                )
            )
            continue

        visible_words = [w for w in norm_visible.split() if len(w) >= 3]

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

    if not applicable_found:
        issues.append(
            make_issue(
                criterion="2.5.3",
                issue="label_in_name_not_applicable",
                location={},
                severity="not_applicable",
                recommendation="No applicable form controls with detectable programmatic names and visible labels were found for label-in-name checking."
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
        issues.append(
            make_issue(
                criterion="2.5.1",
                issue="pointer_gesture_not_applicable",
                location={},
                severity="not_applicable",
                recommendation=(
                    "No scripted, rich media, or signature-based interaction was detected that would make pointer gesture review applicable."
                ),
            )
        )
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

#2.5.2
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
        issues.append(
            make_issue(
                criterion="2.5.2",
                issue="pointer_cancellation_not_applicable",
                location={},
                severity="not_applicable",
                recommendation=(
                    "No scripted, submit-like, or validation-triggered interaction was detected that would make pointer cancellation review applicable."
                ),
            )
        )
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

    motion_keywords = {"motion", "tilt", "shake", "orientation", "accelerometer", "gyro", "gyroscope"}
    trigger_text = " ".join(
        str(t.get("trigger", "")) + " " + str(t.get("location", ""))
        for t in javascript_triggers
        if isinstance(t, dict)
    ).lower()

    has_motion_signal = any(k in trigger_text for k in motion_keywords)

    if not has_javascript and not has_richmedia and not has_motion_signal:
        issues.append(
            make_issue(
                criterion="2.5.4",
                issue="motion_actuation_not_applicable",
                location={},
                severity="not_applicable",
                recommendation=(
                    "No scripted, rich media, or motion-related interaction was detected that would make motion actuation review applicable."
                ),
            )
        )
        return issues

    if has_motion_signal or has_richmedia:
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
                    "This PDF may contain interaction that could depend on motion or advanced embedded behavior. "
                    "Verify that any motion-based functionality can also be operated through user interface components "
                    "and that motion response can be disabled, unless essential."
                ),
            )
        )
        return issues

    issues.append(
        make_issue(
            criterion="2.5.4",
            issue="motion_actuation_not_detected",
            location={
                "has_javascript": has_javascript,
                "javascript_triggers": javascript_triggers,
                "has_richmedia": has_richmedia,
                "has_motion_signal": has_motion_signal,
            },
            severity="pass",
            recommendation=(
                "Scripted interaction was detected, but no specific motion-related signal was found."
            ),
        )
    )
    return issues




 
def run_batch1_rules(document: dict) -> list[dict]:
    issues: list[dict] = []
    issues.extend(rule_1_1_1(document))
    issues.extend(rule_1_2_1(document))
    issues.extend(rule_1_2_2(document))
    issues.extend(rule_1_2_3(document))
    issues.extend(rule_1_2_4(document))
    issues.extend(rule_1_2_5(document))
    issues.extend(rule_1_4_1(document))
    issues.extend(rule_1_4_3(document))
    issues.extend(rule_1_4_4(document))
    issues.extend(rule_1_4_11(document))
    issues.extend(rule_2_5_1(document))
    issues.extend(rule_2_5_2(document))
    issues.extend(rule_2_5_3(document))
    issues.extend(rule_2_5_4(document))
    return issues
