from ....stages.detection.issue import make_issue
from ....utils.geometry_helper import bbox_intersects,_union_bboxes
from ....utils.graphics_helper import has_underline_graphic, has_enclosing_box_cue
from ....utils.text_helper import (
    is_url_like_text,_sort_spans_reading_order
)
from ....utils.widget_helper import _collect_near_widget_label_spans
from ....utils.link_helpers import collect_same_line_non_link_neighbors
from ....utils.font_helper import  similar_font_properties
from ....utils.color_helper import colors_are_distinct,_is_redish
from ....utils.marker_helper import (
    _is_marker_or_identical_label_candidate, _same_pattern_axis,_normalize_repeat_text,
    _collect_nearby_text)

from ....utils.patterns import( EXPLICIT_COLOR_ONLY_PATTERNS,
                               _REQUIRED_CUE_PATTERN,MARKER_TEXTS)



def detect_link_color_only(document: dict) -> list[dict]:
    issues: list[dict] = []
    doc = document.get("document", document)

    text_spans = doc.get("text_spans", [])
    links = doc.get("links", [])
    graphics = doc.get("graphics", [])

    links_by_page: dict[int, list[dict]] = {}
    graphics_by_page: dict[int, list[dict]] = {}
    spans_by_page: dict[int, list[dict]] = {}

    for link in links:
        p = link.get("page_index")
        if p is not None:
            links_by_page.setdefault(p, []).append(link)

    for g in graphics:
        p = g.get("page_index")
        if p is not None:
            graphics_by_page.setdefault(p, []).append(g)

    for sp in text_spans:
        p = sp.get("page_index")
        if p is not None:
            spans_by_page.setdefault(p, []).append(sp)

    reported_link_ids = set()

    for page_index, page_links in links_by_page.items():
        page_spans = spans_by_page.get(page_index, [])
        page_graphics = graphics_by_page.get(page_index, [])

        for link in page_links:
            link_id = link.get("id")
            link_bbox = link.get("bbox")

            if not link_id or not link_bbox or link_id in reported_link_ids:
                continue

            linked_spans = [
                sp for sp in page_spans
                if sp.get("bbox")
                and (sp.get("text") or "").strip()
                and bbox_intersects(sp["bbox"], link_bbox, margin=2.0)
            ]

            if not linked_spans:
                issues.append(
                    make_issue(
                        criterion="1.4.1",
                        issue="link_color_only_needs_review",
                        location={
                            "page": page_index,
                            "link_id": link_id,
                        },
                        severity="needs_review",
                        recommendation="Linked text could not be determined automatically."
                    )
                )
                reported_link_ids.add(link_id)
                continue

            linked_spans = _sort_spans_reading_order(linked_spans)

            combined_text = " ".join(
                (sp.get("text") or "").strip()
                for sp in linked_spans
                if (sp.get("text") or "").strip()
            ).strip()

            if not combined_text:
                issues.append(
                    make_issue(
                        criterion="1.4.1",
                        issue="link_color_only_needs_review",
                        location={
                            "page": page_index,
                            "link_id": link_id,
                            "span_ids": [sp.get("id") for sp in linked_spans if sp.get("id")],
                        },
                        severity="needs_review",
                        recommendation="Linked label text could not be interpreted automatically."
                    )
                )
                reported_link_ids.add(link_id)
                continue

            if is_url_like_text(combined_text):
                issues.append(
                    make_issue(
                        criterion="1.4.1",
                        issue="link_non_color_cue_detected",
                        location={
                            "page": page_index,
                            "link_id": link_id,
                            "span_ids": [sp.get("id") for sp in linked_spans if sp.get("id")],
                        },
                        severity="pass",
                        recommendation="Link text itself provides a visible cue."
                    )
                )
                reported_link_ids.add(link_id)
                continue

            combined_bbox = _union_bboxes([sp["bbox"] for sp in linked_spans if sp.get("bbox")])
            if not combined_bbox:
                issues.append(
                    make_issue(
                        criterion="1.4.1",
                        issue="link_color_only_needs_review",
                        location={
                            "page": page_index,
                            "link_id": link_id,
                            "span_ids": [sp.get("id") for sp in linked_spans if sp.get("id")],
                        },
                        severity="needs_review",
                        recommendation="Link region could not be computed automatically."
                    )
                )
                reported_link_ids.add(link_id)
                continue

            if has_underline_graphic(combined_bbox, page_graphics):
                issues.append(
                    make_issue(
                        criterion="1.4.1",
                        issue="link_non_color_cue_detected",
                        location={
                            "page": page_index,
                            "link_id": link_id,
                            "span_ids": [sp.get("id") for sp in linked_spans if sp.get("id")],
                        },
                        severity="pass",
                        recommendation="A non-color cue was detected."
                    )
                )
                reported_link_ids.add(link_id)
                continue

            if has_enclosing_box_cue(combined_bbox, page_graphics):
                issues.append(
                    make_issue(
                        criterion="1.4.1",
                        issue="link_non_color_cue_detected",
                        location={
                            "page": page_index,
                            "link_id": link_id,
                            "span_ids": [sp.get("id") for sp in linked_spans if sp.get("id")],
                        },
                        severity="pass",
                        recommendation="A non-color cue was detected."
                    )
                )
                reported_link_ids.add(link_id)
                continue

            rep_span = linked_spans[0]
            rep_font = rep_span.get("font", {})
            rep_color = rep_span.get("color", {}).get("fill_rgb")

            if not rep_color:
                issues.append(
                    make_issue(
                        criterion="1.4.1",
                        issue="link_color_only_needs_review",
                        location={
                            "page": page_index,
                            "link_id": link_id,
                            "span_ids": [sp.get("id") for sp in linked_spans if sp.get("id")],
                        },
                        severity="needs_review",
                        recommendation="Link color could not be determined automatically."
                    )
                )
                reported_link_ids.add(link_id)
                continue

            neighbor_map: dict[str, dict] = {}

            for lsp in linked_spans:
                neighbors = collect_same_line_non_link_neighbors(
                    target_span=lsp,
                    text_spans=page_spans,
                    page_links=[link],
                    max_horizontal_distance=220.0,
                )
                for n in neighbors:
                    nid = n.get("id")
                    if nid:
                        neighbor_map[nid] = n

            if not neighbor_map:
                issues.append(
                    make_issue(
                        criterion="1.4.1",
                        issue="link_color_only_needs_review",
                        location={
                            "page": page_index,
                            "link_id": link_id,
                            "span_ids": [sp.get("id") for sp in linked_spans if sp.get("id")],
                        },
                        severity="needs_review",
                        recommendation="Comparable surrounding text was not found automatically."
                    )
                )
                reported_link_ids.add(link_id)
                continue

            typography_neighbors = []
            for n in neighbor_map.values():
                n_text = (n.get("text") or "").strip()
                n_color = n.get("color", {}).get("fill_rgb")
                n_font = n.get("font", {})

                if not n_text or not n_color:
                    continue

                if similar_font_properties(rep_font, n_font, size_tol=1.0):
                    typography_neighbors.append(n)

            if not typography_neighbors:
                issues.append(
                    make_issue(
                        criterion="1.4.1",
                        issue="link_color_only_passed",
                        location={
                            "page": page_index,
                            "link_id": link_id,
                            "span_ids": [sp.get("id") for sp in linked_spans if sp.get("id")],
                        },
                        severity="pass",
                        recommendation="No color-only failure was detected."
                    )
                )
                reported_link_ids.add(link_id)
                continue

            distinct_neighbors = []
            for n in typography_neighbors:
                n_color = n.get("color", {}).get("fill_rgb")
                if colors_are_distinct(rep_color, n_color):
                    distinct_neighbors.append(n)

            if not distinct_neighbors:
                issues.append(
                    make_issue(
                        criterion="1.4.1",
                        issue="link_color_only_passed",
                        location={
                            "page": page_index,
                            "link_id": link_id,
                            "span_ids": [sp.get("id") for sp in linked_spans if sp.get("id")],
                        },
                        severity="pass",
                        recommendation="No color-only failure was detected."
                    )
                )
                reported_link_ids.add(link_id)
                continue

            issues.append(
                make_issue(
                    criterion="1.4.1",
                    issue="link_distinguished_by_color_only",
                    location={
                        "page": page_index,
                        "link_id": link_id,
                        "span_ids": [sp.get("id") for sp in linked_spans if sp.get("id")],
                    },
                    severity="high",
                    recommendation="Add a non-color cue such as underlining.",
                )
            )
            reported_link_ids.add(link_id)

    return issues


def detect_explicit_color_only_instructions(document: dict) -> list[dict]:
    issues: list[dict] = []
    doc = document.get("document", document)

    text_blocks = doc.get("text_blocks", [])
    text_spans = doc.get("text_spans", [])

    reported_keys = set()
    found_any = False

    for block in text_blocks:
        block_id = block.get("id")
        text = (block.get("text") or "").strip()
        page_index = block.get("page_index")

        if not text:
            continue

        for pattern in EXPLICIT_COLOR_ONLY_PATTERNS:
            if pattern.search(text):
                key = ("block", block_id)
                if key in reported_keys:
                    break

                issues.append(
                    make_issue(
                        criterion="1.4.1",
                        issue="explicit_color_only_instruction",
                        location={
                            "page": page_index,
                            "block_id": block_id,
                        },
                        severity="high",
                        recommendation="Do not rely on color alone; add another cue.",
                    )
                )
                reported_keys.add(key)
                found_any = True
                break

    for span in text_spans:
        span_id = span.get("id")
        text = (span.get("text") or "").strip()
        page_index = span.get("page_index")

        if not span_id or not text:
            continue

        for pattern in EXPLICIT_COLOR_ONLY_PATTERNS:
            if pattern.search(text):
                key = ("span", span_id)
                if key in reported_keys:
                    break

                issues.append(
                    make_issue(
                        criterion="1.4.1",
                        issue="explicit_color_only_instruction",
                        location={
                            "page": page_index,
                            "span_id": span_id,
                        },
                        severity="high",
                        recommendation="Do not rely on color alone; add another cue.",
                    )
                )
                reported_keys.add(key)
                found_any = True
                break

    if not found_any:
        issues.append(
            make_issue(
                criterion="1.4.1",
                issue="explicit_color_only_instruction_not_detected",
                location={},
                severity="pass",
                recommendation="No explicit color-only instruction was detected."
            )
        )

    return issues


def detect_required_field_color_only(document: dict) -> list[dict]:
    issues: list[dict] = []
    doc = document.get("document", document)

    text_spans = doc.get("text_spans", [])
    widgets = doc.get("widgets", [])

    spans_by_page: dict[int, list[dict]] = {}
    widgets_by_page: dict[int, list[dict]] = {}

    for sp in text_spans:
        p = sp.get("page_index")
        if p is not None:
            spans_by_page.setdefault(p, []).append(sp)

    for w in widgets:
        p = w.get("page_index")
        if p is not None:
            widgets_by_page.setdefault(p, []).append(w)

    reported_span_ids = set()
    found_any = False

    for page_index, page_widgets in widgets_by_page.items():
        page_spans = spans_by_page.get(page_index, [])

        for widget in page_widgets:
            candidate_labels = _collect_near_widget_label_spans(widget, page_spans)

            if not candidate_labels:
                continue

            for sp in candidate_labels:
                span_id = sp.get("id")
                if not span_id or span_id in reported_span_ids:
                    continue

                sp_text = (sp.get("text") or "").strip()
                sp_bbox = sp.get("bbox")
                sp_color = sp.get("color", {}).get("fill_rgb")
                sp_font = sp.get("font", {})

                if not sp_text or not sp_bbox or not sp_color:
                    continue

                if len(sp_text.split()) > 6:
                    continue

                if not _is_redish(sp_color):
                    continue

                if _REQUIRED_CUE_PATTERN.search(sp_text):
                    continue

                nearby_text_spans = _collect_nearby_text(sp, page_spans, max_h_gap=90.0, max_v_gap=20.0)
                nearby_text = " ".join((n.get("text") or "").strip() for n in nearby_text_spans)

                if _REQUIRED_CUE_PATTERN.search(nearby_text):
                    continue

                typography_similar_neighbors = []
                for n in nearby_text_spans:
                    n_text = (n.get("text") or "").strip()
                    n_color = n.get("color", {}).get("fill_rgb")
                    n_font = n.get("font", {})

                    if not n_text or not n_color:
                        continue

                    if similar_font_properties(sp_font, n_font, size_tol=1.0):
                        typography_similar_neighbors.append(n)

                distinct_neighbors = []
                for n in typography_similar_neighbors:
                    n_color = n.get("color", {}).get("fill_rgb")
                    if colors_are_distinct(sp_color, n_color):
                        distinct_neighbors.append(n)

                if not distinct_neighbors:
                    continue

                issues.append(
                    make_issue(
                        criterion="1.4.1",
                        issue="required_field_indicated_by_color_only",
                        location={
                            "page": page_index,
                            "span_id": span_id,
                            "widget_id": widget.get("id"),
                        },
                        severity="high",
                        recommendation="Add a cue such as '*' or 'required'.",
                    )
                )
                reported_span_ids.add(span_id)
                found_any = True

    if not found_any:
        issues.append(
            make_issue(
                criterion="1.4.1",
                issue="required_field_color_only_not_detected",
                location={},
                severity="pass",
                recommendation="No action needed since no required field marked only by color was detected."
            )
        )

    return issues


def detect_repeated_identical_marker_or_label_color_only(document: dict) -> list[dict]:
    """
    Detect repeated identical markers/labels whose distinction appears
    to rely mainly on color.
    """
    issues: list[dict] = []
    doc = document.get("document", document)

    text_spans = doc.get("text_spans", [])
    links = doc.get("links", [])
    graphics = doc.get("graphics", [])

    spans_by_page: dict[int, list[dict]] = {}
    links_by_page: dict[int, list[dict]] = {}
    graphics_by_page: dict[int, list[dict]] = {}

    for sp in text_spans:
        p = sp.get("page_index")
        if p is not None:
            spans_by_page.setdefault(p, []).append(sp)

    for lk in links:
        p = lk.get("page_index")
        if p is not None:
            links_by_page.setdefault(p, []).append(lk)

    for g in graphics:
        p = g.get("page_index")
        if p is not None:
            graphics_by_page.setdefault(p, []).append(g)

    reported_group_keys = set()
    found_any = False

    for page_index, page_spans in spans_by_page.items():
        page_links = links_by_page.get(page_index, [])
        page_graphics = graphics_by_page.get(page_index, [])

        groups: dict[str, list[dict]] = {}

        for sp in page_spans:
            text = (sp.get("text") or "").strip()
            bbox = sp.get("bbox")
            color = sp.get("color", {}).get("fill_rgb")
            sem = sp.get("presentation_semantics", {})

            if not text or not bbox or not color:
                continue

            if sem.get("is_logo_text", False):
                continue
            if sem.get("is_decorative_text", False):
                continue
            if sem.get("is_text_in_image_context", False):
                continue

            if any(
                lk.get("bbox") and bbox_intersects(bbox, lk["bbox"], margin=2.0)
                for lk in page_links
            ):
                continue

            if has_enclosing_box_cue(bbox, page_graphics):
                continue

            if not _is_marker_or_identical_label_candidate(text):
                continue

            nt = _normalize_repeat_text(text)
            group_key = text if text in MARKER_TEXTS else nt
            groups.setdefault(group_key, []).append(sp)

        for group_key, group_spans in groups.items():
            is_marker_group = group_key in MARKER_TEXTS
            min_group_size = 2 if is_marker_group else 3

            if len(group_spans) < min_group_size:
                continue

            base_font = group_spans[0].get("font", {})
            consistent = [
                sp for sp in group_spans
                if similar_font_properties(base_font, sp.get("font", {}), size_tol=1.0)
            ]

            if len(consistent) < min_group_size:
                continue

            patterned = []
            for sp in consistent:
                sb = sp.get("bbox")
                if not sb:
                    continue

                aligned_peers = 0
                for other in consistent:
                    if other.get("id") == sp.get("id"):
                        continue
                    ob = other.get("bbox")
                    if not ob:
                        continue
                    if _same_pattern_axis(sb, ob):
                        aligned_peers += 1

                if aligned_peers >= 1:
                    patterned.append(sp)

            if len(patterned) < min_group_size:
                continue

            color_buckets: dict[tuple[int, int, int], list[dict]] = {}
            for sp in patterned:
                c = sp.get("color", {}).get("fill_rgb")
                if not c or len(c) < 3:
                    continue
                key = (int(c[0]), int(c[1]), int(c[2]))
                color_buckets.setdefault(key, []).append(sp)

            if len(color_buckets) < 2:
                continue

            distinct_color_keys = list(color_buckets.keys())
            has_clear_difference = False
            for i in range(len(distinct_color_keys)):
                for j in range(i + 1, len(distinct_color_keys)):
                    if colors_are_distinct(
                        list(distinct_color_keys[i]),
                        list(distinct_color_keys[j]),
                    ):
                        has_clear_difference = True
                        break
                if has_clear_difference:
                    break

            if not has_clear_difference:
                continue

            report_key = (page_index, group_key)
            if report_key in reported_group_keys:
                continue

            issues.append(
                make_issue(
                    criterion="1.4.1",
                    issue="repeated_identical_marker_or_label_distinguished_by_color_only",
                    location={
                        "page": page_index,
                        "group_text": group_key,
                        "span_ids": [sp.get("id") for sp in patterned if sp.get("id")],
                    },
                    severity="medium",
                    recommendation="Add a non-color cue to distinguish these items.",
                )
            )
            reported_group_keys.add(report_key)
            found_any = True

    if not found_any:
        issues.append(
            make_issue(
                criterion="1.4.1",
                issue="repeated_identical_marker_or_label_color_only_not_detected",
                location={},
                severity="pass",
                recommendation="No repeated identical items distinguished only by color were detected."
            )
        )

    return issues
