from .issue import make_issue
from backend.app.services.helper_function_b1 import ( detect_link_color_only,detect_explicit_color_only_instructions,detect_required_field_color_only,detect_repeated_identical_marker_or_label_color_only)
    



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

#############
#WCAG 1.2
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


# rule 1.4.4
# 1.4.4 resize issue
def rule_1_4_4(document: dict) -> list[dict]:
        issues: list[dict] = []
        doc = document.get("document", document)

        text_spans = doc.get("text_spans", [])
        for text_span in text_spans:
            resize = text_span.get("resize_risk", {})
            risk_score = resize.get("risk_score", 0)

            if risk_score >= 6:
                severity = "high"
            elif risk_score >= 4:
                severity = "medium"
            elif risk_score >= 2:
                severity = "low"
            else:
                severity = None

            if severity:
                issues.append(
                    make_issue(
                    criterion="1.4.4",
                    issue="potential_resize_issue",
                    location={
                        "page": text_span.get("page_index"),
                        "span_id": text_span.get("id"),
                    },
                    severity=severity,
                    recommendation="Ensure that text can be resized up to 200% without loss of content or functionality. Check that text does not get cut off, overlap, or become unreadable when enlarged.",
                )
            )




def rule_1_4_11(document: dict) -> list[dict]:
    issues: list[dict] = []
    doc = document.get("document", document)

    graphics = doc.get("graphics", [])
    widgets = doc.get("widgets", [])

    for graphic in graphics:
        ntc = graphic.get("non_text_contrast", {})
        passed = ntc.get("passes_3_1", True)

        if passed is False:
            issues.append(
                make_issue(
                    criterion="1.4.11",
                    issue="insufficient_non_text_contrast_graphic",
                    location={
                        "page": graphic.get("page_index"),
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
        passed = ntc.get("passes_3_1", True)

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


def run_batch2_rules(document: dict) -> list[dict]:
    issues: list[dict] = []
    doc = document.get("document", document)

    
    # 1.4.1 Use of Color
    issues.extend(rule_1_4_1(document))

    # 1.4.11 Non-text Contrast
    issues.extend(rule_1_4_11(document))

    # 1.4.4 resize risk
    issues.extend(rule_1_4_4(document))

    return issues  