import logging
import pikepdf
from ...utils.correction import (
    _filter_issues,
    _get_doc,
    _skipped,
    _fixed,
    _flagged,
    _build_span_lookup,
    _find_acroform_field,
    _set_tooltip_everywhere,
)
from .utils.correction_utils import (
    _find_acroform_field_by_bbox,
    _find_accessible_color,
    _try_recolor_in_stream,
)
from ...utils.color_helper import contrast_ratio

logger = logging.getLogger(__name__)

def fix_1_1_1_image_alt_text(
    pdf: pikepdf.Pdf,
    issues: list[dict],
    doc_json: dict,
    original_pdf_path: str,
) -> list[dict]:
    """
    WCAG 1.1.1 - image_missing_text_alternative
    Owner: Jana | Method: GPT-4o vision
    """
    import base64
    import fitz
    from app.services.openai_client import get_openai_client

    results = []
    issue_key = "image_missing_text_alternative"
    targets = _filter_issues(issues, "1.1.1", issue_key)

    if not targets:
        return results

    doc_data = _get_doc(doc_json)
    occurrences = (
        doc_data.get("images", {}).get("occurrences", [])
        or doc_data.get("image_occurrences", [])
        or []
    )
    text_spans = doc_data.get("text_spans", [])
    occ_by_id = {occ.get("id"): occ for occ in occurrences if occ.get("id")}

    try:
        fitz_doc = fitz.open(original_pdf_path)
    except Exception as exc:
        for iss in targets:
            results.append(_skipped("1.1.1", issue_key, f"Could not open PDF: {exc}"))
        return results

    try:
        client = get_openai_client()
    except Exception as exc:
        fitz_doc.close()
        for iss in targets:
            results.append(_skipped("1.1.1", issue_key, f"OpenAI client unavailable: {exc}"))
        return results

    def _extract_image_bytes(page_index: int, occ_bbox: list) -> bytes | None:
        try:
            page = fitz_doc.load_page(page_index)
            target_rect = fitz.Rect(occ_bbox)
            best_xref = None
            best_overlap = 0
            for img in page.get_images(full=True):
                xref = img[0]
                rects = page.get_image_rects(xref)
                for rect in rects:
                    overlap = rect & target_rect
                    overlap_area = overlap.get_area() if not overlap.is_empty else 0
                    if overlap_area > best_overlap:
                        best_overlap = overlap_area
                        best_xref = xref
            if best_xref is None:
                return None
            return fitz_doc.extract_image(best_xref).get("image")
        except Exception as exc:
            logger.debug("_extract_image_bytes failed: %s", exc)
            return None

    def _nearby_text(page_index: int, bbox: list) -> str:
        if not bbox:
            return ""
        x0, y0, x1, y1 = bbox
        expanded = fitz.Rect(x0 - 80, y0 - 80, x1 + 80, y1 + 80)
        nearby = []
        for span in text_spans:
            if span.get("page_index") != page_index:
                continue
            span_bbox = span.get("bbox")
            text = span.get("text", "").strip()
            if not span_bbox or not text:
                continue
            if expanded.intersects(fitz.Rect(span_bbox)):
                nearby.append(text)
        return " ".join(nearby)[:500]

    def _find_figure_node(struct_figure_id: str):
        """Recursively walk StructTreeRoot to find Figure node matching MCIDs."""
        try:
            figures = _get_doc(doc_json).get("structure", {}).get("figures", [])
            target_fig = next(
                (f for f in figures if f.get("id") == struct_figure_id),
                None
            )
            if target_fig is None:
                return None

            target_mcids = target_fig.get("mcids", [])

            root = pdf.Root.get("/StructTreeRoot")
            if root is None:
                return None

            def walk(node):
                try:
                    if not isinstance(node, pikepdf.Dictionary):
                        try:
                            node = node.get_object()
                        except Exception:
                            return None
                    if not isinstance(node, pikepdf.Dictionary):
                        return None

                    s_type = str(node.get("/S", ""))
                    if s_type == "/Figure":
                        k = node.get("/K")
                        if k is not None:
                            node_mcids = []
                            if isinstance(k, pikepdf.Array):
                                for item in k:
                                    try:
                                        node_mcids.append(int(item))
                                    except Exception:
                                        pass
                            else:
                                try:
                                    node_mcids.append(int(k))
                                except Exception:
                                    pass
                            if any(m in node_mcids for m in target_mcids):
                                return node

                    kids = node.get("/K")
                    if kids is None:
                        return None
                    if isinstance(kids, pikepdf.Array):
                        for kid in kids:
                            try:
                                if isinstance(kid, (int, float)):
                                    continue
                                found = walk(kid)
                                if found is not None:
                                    return found
                            except Exception:
                                continue
                except Exception:
                    pass
                return None

            try:
                return walk(root)
            except Exception as exc:
                logger.debug("_find_figure_node walk failed: %s", exc)
                return None

        except Exception as exc:
            logger.debug("_find_figure_node failed: %s", exc)
            return None

    def _ask_ai_for_alt_text(img_bytes: bytes, nearby_text: str) -> str | None:
        try:
            b64_image = base64.b64encode(img_bytes).decode("utf-8")
            prompt = (
                "Describe this image in one concise sentence suitable as alt text "
                "for a PDF. Maximum 125 characters. "
                f"Nearby page context: {nearby_text or 'No nearby text available.'} "
                "Return ONLY the alt text. No explanation. No quotes."
            )
            response = client.chat.completions.create(
                model="gpt-4o",
                temperature=0,
                max_tokens=60,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{b64_image}",
                                "detail": "low",
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }],
            )
            alt_text = response.choices[0].message.content.strip().strip('"').strip("'")
            bad_phrases = ["i cannot", "i can't", "sorry", "unable to", "cannot determine"]
            if not alt_text or len(alt_text) > 200 or "\n" in alt_text:
                return None
            if any(phrase in alt_text.lower() for phrase in bad_phrases):
                return None
            return alt_text
        except Exception as exc:
            logger.warning("AI alt text generation failed: %s", exc)
            return None

    for iss in targets:
        try:
            loc = iss.get("location", {})
            image_id = loc.get("image_id")
            page_index = loc.get("page", loc.get("page_index"))

            occ = occ_by_id.get(image_id)
            if occ is None:
                results.append(_skipped("1.1.1", issue_key, f"Image occurrence '{image_id}' not found"))
                continue

            if page_index is None:
                page_index = occ.get("page_index", occ.get("page"))

            bbox = occ.get("bbox")
            struct_figure_id = occ.get("struct_figure_id")

            if page_index is None or not bbox:
                results.append(_skipped("1.1.1", issue_key, "Missing page index or bbox"))
                continue

            img_bytes = _extract_image_bytes(page_index, bbox)
            if not img_bytes:
                results.append(_skipped("1.1.1", issue_key, "Could not extract image bytes"))
                continue

            nearby = _nearby_text(page_index, bbox)
            alt_text = _ask_ai_for_alt_text(img_bytes, nearby)

            if not alt_text:
                results.append(_skipped("1.1.1", issue_key, "AI could not generate usable alt text"))
                continue

            # Helper to create a Figure struct element and attach to struct tree
            def _create_figure_node(alt: str) -> "pikepdf.Dictionary | None":
                try:
                    struct_root = pdf.Root.get("/StructTreeRoot")
                    if struct_root is None:
                        # Create minimal StructTreeRoot
                        struct_root = pdf.make_indirect(pikepdf.Dictionary(
                            Type=pikepdf.Name("/StructTreeRoot"),
                        ))
                        pdf.Root["/StructTreeRoot"] = struct_root
                        pdf.Root["/MarkInfo"] = pikepdf.Dictionary(
                            Marked=pikepdf.Boolean(True)
                        )

                    figure_node = pdf.make_indirect(pikepdf.Dictionary(
                        Type=pikepdf.Name("/StructElem"),
                        S=pikepdf.Name("/Figure"),
                        Alt=pikepdf.String(alt),
                        P=struct_root,
                    ))

                    # Attach to struct tree root's /K array
                    existing_k = struct_root.get("/K")
                    if existing_k is None:
                        struct_root["/K"] = pikepdf.Array([figure_node])
                    elif isinstance(existing_k, pikepdf.Array):
                        existing_k.append(figure_node)
                    else:
                        struct_root["/K"] = pikepdf.Array([existing_k, figure_node])

                    return figure_node
                except Exception as exc:
                    logger.debug("_create_figure_node failed: %s", exc)
                    return None

            if not struct_figure_id:
                # No Figure node linked — create one
                figure_node = _create_figure_node(alt_text)
                if figure_node is None:
                    results.append(_skipped("1.1.1", issue_key,
                        "No linked Figure node and could not create one"))
                    continue
                results.append(_fixed("1.1.1", issue_key,
                    f"Created Figure node with /Alt='{alt_text}' via GPT-4o vision"))
                continue

            figure_node = _find_figure_node(struct_figure_id)
            if figure_node is None:
                # Node ID exists in doc_json but not found in tree — create it
                figure_node = _create_figure_node(alt_text)
                if figure_node is None:
                    results.append(_skipped("1.1.1", issue_key,
                        f"Figure node '{struct_figure_id}' not found and could not be created"))
                    continue
                results.append(_fixed("1.1.1", issue_key,
                    f"Created missing Figure node with /Alt='{alt_text}' via GPT-4o vision"))
                continue

            figure_node["/Alt"] = pikepdf.String(alt_text)
            results.append(_fixed("1.1.1", issue_key,
                f"Set image /Alt='{alt_text}' via GPT-4o vision"))

        except Exception as exc:
            results.append(_skipped("1.1.1", issue_key, f"Error: {exc}"))

    fitz_doc.close()
    return results





def fix_1_1_1_control_name(
    pdf: pikepdf.Pdf,
    issues: list[dict],
    doc_json: dict,
    original_pdf_path: str,
) -> list[dict]:
    import base64
    import fitz
    from app.services.openai_client import get_openai_client

    results = []
    targets = _filter_issues(issues, "1.1.1", "control_missing_name")
    if not targets:
        return results

    doc_data = _get_doc(doc_json)
    widgets  = doc_data.get("widgets", [])
    widget_by_id = {w["id"]: w for w in widgets if w.get("id")}

    try:
        fitz_doc = fitz.open(original_pdf_path)
    except Exception as exc:
        for iss in targets:
            results.append(_skipped("1.1.1", "control_missing_name", f"Could not open PDF: {exc}"))
        return results
    # get OpenAI client
    try:
        client = get_openai_client()
    except RuntimeError as exc:
        for iss in targets:
            results.append(_skipped("1.1.1", "control_missing_name", str(exc)))
        fitz_doc.close()
        return results

    def _screenshot_around_widget(page_index: int, bbox: list) -> bytes | None:
        """Render a small region around the widget so GPT-4o can see its visual context."""
        try:
            page = fitz_doc.load_page(page_index)
            if bbox:
                x0, y0, x1, y1 = bbox
                # expand region a bit so nearby label text is visible
                clip = fitz.Rect(x0 - 60, y0 - 40, x1 + 60, y1 + 40)
            else:
                clip = page.rect

            pix = page.get_pixmap(clip=clip, dpi=100)
            return pix.tobytes("png")
        except Exception as exc:
            logger.debug("_screenshot_around_widget: %s", exc)
            return None

    for iss in targets:
        issue_key = "control_missing_name"
        try:
            loc       = iss.get("location", {})
            widget_id = loc.get("widget_id")

            # ── find widget ──────────────────────────────────────────────
            widget = widget_by_id.get(widget_id)
            if widget is None:
                results.append(_skipped("1.1.1", issue_key, f"Widget '{widget_id}' not found"))
                continue

            # ── find field in PDF ────────────────────────────────────────
            field_name = widget.get("field_name")
            # ── get widget location (needed for both field lookup and screenshot) ──
            page_index = widget.get("page_index")
            bbox       = widget.get("bbox")

            # ── find field in PDF ────────────────────────────────────────
            field_obj  = _find_acroform_field(pdf, field_name) if field_name else None
            if field_obj is None and page_index is not None and bbox:
                field_obj = _find_acroform_field_by_bbox(pdf, page_index, bbox)
            if field_obj is None:
                results.append(_skipped("1.1.1", issue_key, "Field not found in PDF"))
                continue
            img_bytes  = _screenshot_around_widget(page_index, bbox)

            if not img_bytes:
                results.append(_skipped("1.1.1", issue_key, "Could not render widget region"))
                continue

            # ── call GPT-4o vision ───────────────────────────────────────
            b64_image   = base64.standard_b64encode(img_bytes).decode("utf-8")
            field_type  = widget.get("field_type") or "text field"
            field_name_hint = field_name or "unknown"

            prompt = (
                f"This is a cropped region of a PDF form. "
                f"There is a form field ({field_type}) with internal name '{field_name_hint}'. "
                f"Look at the visible label text near the field and suggest a short, clear, "
                f"human-readable accessible name for it (max 60 characters). "
                f"Examples: 'First Name', 'Email Address', 'Date of Birth'. "
                f"Return ONLY the accessible name. No explanation. No quotes."
            )

            response = client.chat.completions.create(
                model="gpt-4o",
                max_tokens=30,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{b64_image}",
                                "detail": "low",
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }],
            )

            accessible_name = response.choices[0].message.content.strip()

            # ── validate response ────────────────────────────────────────
            if not accessible_name or len(accessible_name) > 100 or "\n" in accessible_name:
                results.append(_skipped("1.1.1", issue_key,
                                        f"GPT-4o returned unusable name: '{accessible_name}'"))
                continue

            # ── write /TU into the PDF ───────────────────────────────────
            _set_tooltip_everywhere(field_obj, accessible_name)
            results.append(_fixed("1.1.1", issue_key,
                                  f"Set /TU='{accessible_name}' via GPT-4o vision"))

        except Exception as exc:
            results.append(_skipped("1.1.1", issue_key, f"Error: {exc}"))

    fitz_doc.close()
    return results

def fix_1_4_3_contrast(
    pdf: pikepdf.Pdf,
    issues: list[dict],
    doc_json: dict,
    original_pdf_path: str,
) -> list[dict]:
    results = []
    issue_key = "insufficient_text_contrast"

    targets = _filter_issues(issues, "1.4.3", issue_key)
    if not targets:
        return results

    span_lookup = _build_span_lookup(doc_json)

    recolor_cache: dict[tuple, bool] = {}

    for iss in targets:
        try:
            loc     = iss.get("location", {})
            span_id = loc.get("span_id")
            span    = span_lookup.get(span_id)

            if span is None:
                results.append(_skipped("1.4.3", issue_key, f"Span '{span_id}' not found"))
                continue

            fg_rgb     = span.get("color", {}).get("fill_rgb") or span.get("fill_rgb")
            bg_rgb     = span.get("background_estimate", {}).get("bg_rgb") or span.get("bg_rgb")
            large_text = span.get("contrast", {}).get("large_text_assumed", False)

            if not fg_rgb or not bg_rgb:
                results.append(_skipped("1.4.3", issue_key, "Missing fg or bg RGB"))
                continue

            required_ratio = 3.0 if large_text else 4.5
            target_ratio   = 4.0 if large_text else 6.0
            current_ratio  = contrast_ratio(fg_rgb, bg_rgb)

            if current_ratio >= required_ratio:
                results.append(_fixed("1.4.3", issue_key, f"Already passes: {current_ratio:.2f}:1"))
                continue

            new_fg_rgb = _find_accessible_color(fg_rgb, bg_rgb, target_ratio)
            if new_fg_rgb is None:
                results.append(_skipped("1.4.3", issue_key, "Could not compute accessible color"))
                continue

            page_index = span.get("page_index")
            new_ratio  = contrast_ratio(new_fg_rgb, bg_rgb)
            cache_key = (page_index, tuple(fg_rgb))

            if cache_key in recolor_cache:
                success = recolor_cache[cache_key]
            else:
                success = _try_recolor_in_stream(pdf, page_index, fg_rgb, new_fg_rgb)
                recolor_cache[cache_key] = success

            if success:
                results.append(_fixed(
                    "1.4.3", issue_key,
                    f"Recolored span '{span_id}': RGB {fg_rgb} → {new_fg_rgb} "
                    f"({current_ratio:.2f}:1 → {new_ratio:.2f}:1)"
                ))
            else:
                results.append(_flagged(
                    "1.4.3", issue_key,
                    f"Could not recolor automatically. "
                    f"Manually change RGB {fg_rgb} → {new_fg_rgb} "
                    f"({current_ratio:.2f}:1 → {new_ratio:.2f}:1)"
                ))

        except Exception as exc:
            results.append(_skipped("1.4.3", issue_key, f"Error: {exc}"))

    return results


def fix_1_4_1_color_only(
    pdf: pikepdf.Pdf,
    issues: list[dict],
    doc_json: dict,
    original_pdf_path: str,
) -> list[dict]:
    results = []

    flag_map = {
        "link_distinguished_by_color_only": (
            "Add a non-color cue such as underlining to this link. "
            "Cannot be applied automatically — requires visual content edit."
        ),
        "explicit_color_only_instruction": (
            "Rewrite this instruction to not rely on color alone. "
            "For example replace 'click the green button' with 'click the Submit button'. "
            "Cannot be applied automatically — requires content rewrite."
        ),
        "required_field_indicated_by_color_only": (
            "Add a visible non-color cue such as * or the word 'required' "
            "next to this field label. Cannot be applied automatically — requires visual content edit."
        ),
        "repeated_identical_marker_or_label_distinguished_by_color_only": (
            "Add a non-color distinction such as shape, pattern, or text label "
            "to differentiate these items. Cannot be applied automatically — requires visual redesign."
        ),
    }

    targets = _filter_issues(issues, "1.4.1")
    for iss in targets:
        issue_key = iss.get("issue", "")
        message   = flag_map.get(issue_key)
        if message:
            results.append(_flagged("1.4.1", issue_key, message))

    return results
def fix_1_4_11_non_text_contrast(
    pdf: pikepdf.Pdf,
    issues: list[dict],
    doc_json: dict,
    original_pdf_path: str,
) -> list[dict]:
    """
    WCAG 1.4.11 - Non-text Contrast
    
    Graphics  → cannot fix (content stream edit) → _flagged
    Widgets   → fix via /MK /BC                  → _fixed
    """
    results = []

    doc_data     = _get_doc(doc_json)
    widgets      = doc_data.get("widgets", [])
    widget_by_id = {w["id"]: w for w in widgets if w.get("id")}

    
    # ── graphics: flag only, cannot edit content stream ──────────────────

    graphic_targets = _filter_issues(
        issues, "1.4.11", "insufficient_non_text_contrast_graphic"
    )
    for iss in graphic_targets:
        loc        = iss.get("location", {})
        graphic_id = loc.get("graphic_id")
        page       = loc.get("page")
        results.append(_flagged(
            "1.4.11",
            "insufficient_non_text_contrast_graphic",
            (
                f"Graphic '{graphic_id}' on page {page} has insufficient non-text contrast. "
                f"Manually increase the stroke or fill color contrast to at least 3:1. "
                f"Cannot be fixed automatically — requires content stream edit."
            )
        ))

    # ── widgets: fix via /MK /BC ─────────────────────────────────────────

    widget_targets = _filter_issues(
        issues, "1.4.11", "insufficient_non_text_contrast_ui_component"
    )
    for iss in widget_targets:
        issue_key = "insufficient_non_text_contrast_ui_component"
        loc       = iss.get("location", {})
        widget_id = loc.get("widget_id")

        try:
            # ── find widget in doc_json ───────────────────────────────────
            widget = widget_by_id.get(widget_id)
            if widget is None:
                results.append(_skipped(
                    "1.4.11", issue_key,
                    f"Widget '{widget_id}' not found in doc_json"
                ))
                continue

            # ── get color data ────────────────────────────────────────────
            ntc        = widget.get("non_text_contrast", {})
            border_rgb = ntc.get("border_rgb")
            bg_rgb     = ntc.get("adjacent_rgb")

            if not border_rgb or not bg_rgb:
                results.append(_skipped(
                    "1.4.11", issue_key,
                    f"Widget '{widget_id}' missing border_rgb or adjacent_rgb in non_text_contrast"
                ))
                continue

            # ── compute passing color ─────────────────────────────────────
            current_ratio  = contrast_ratio(border_rgb, bg_rgb)
            new_border_rgb = _find_accessible_color(border_rgb, bg_rgb, target_ratio=3.0)

            if new_border_rgb is None:
                results.append(_skipped(
                    "1.4.11", issue_key,
                    f"Could not compute a passing border color for widget '{widget_id}'"
                ))
                continue

            new_ratio = contrast_ratio(new_border_rgb, bg_rgb)

            # ── find field in PDF ─────────────────────────────────────────
            field_name  = widget.get("field_name")
            field_obj   = _find_acroform_field(pdf, field_name) if field_name else None
            if field_obj is None:
                w_page = widget.get("page_index")
                w_bbox = widget.get("bbox")
                if w_page is not None and w_bbox:
                    field_obj = _find_acroform_field_by_bbox(pdf, w_page, w_bbox)

            if field_obj is None:
                results.append(_skipped(
                    "1.4.11", issue_key,
                    f"AcroForm field '{field_name}' not found in PDF"
                ))
                continue

            # ── write /MK /BC (0-1 range, not 0-255) ─────────────────────
            bc = [pikepdf.Real(round(v / 255, 4)) for v in new_border_rgb]

            mk = field_obj.get("/MK")
            if mk is None:
                field_obj["/MK"] = pikepdf.Dictionary(
                    BC=pikepdf.Array(bc)
                )
            elif isinstance(mk, pikepdf.Dictionary):
                mk["/BC"] = pikepdf.Array(bc)
                # Note: we keep /AP intact so the parser's pixmap-based
                # contrast check still sees a rendered border on re-scan.
                # /NeedAppearances=true tells compliant viewers to regenerate.

                acroform = pdf.Root.get("/AcroForm")
                if acroform is not None:
                    acroform["/NeedAppearances"] = True
            else:
                try:
                    mk_obj = mk.get_object()
                    mk_obj["/BC"] = pikepdf.Array(bc)
                except Exception:
                    field_obj["/MK"] = pikepdf.Dictionary(BC=pikepdf.Array(bc))

            results.append(_fixed(
                "1.4.11",
                issue_key,
                (
                    f"Widget '{widget_id}': border color changed from RGB {border_rgb} "
                    f"to RGB {new_border_rgb}. "
                    f"Contrast improved from {current_ratio:.2f}:1 to {new_ratio:.2f}:1 "
                    f"via /MK /BC."
                )
            ))

        except Exception as exc:
            results.append(_skipped(
                "1.4.11", issue_key,
                f"Error processing widget '{widget_id}': {exc}"
            ))

    return results


def fix_2_5_3_label_in_name(
    pdf: pikepdf.Pdf,
    issues: list[dict],
    doc_json: dict,
    original_pdf_path: str,
) -> list[dict]:
    """
    WCAG 2.5.3 - label_not_in_name
    Owner: Jana | Method: Pure code
    """

    results = []
    issue_key = "label_not_in_name"
    targets = _filter_issues(issues, "2.5.3", issue_key)

    acroform_fields = (
        _get_doc(doc_json)
        .get("interactivity", {})
        .get("acroform_fields", [])
    )

    field_by_id = {f.get("id"): f for f in acroform_fields if f.get("id")}

    for iss in targets:
        loc = iss.get("location", {})
        field_id = loc.get("field_id")
        visible_label = loc.get("visible_label")

        if visible_label is None or visible_label == "":
            results.append(_skipped("2.5.3", issue_key, "No visible label available"))
            continue

        field = field_by_id.get(field_id)

        if field is None:
            results.append(_skipped("2.5.3", issue_key, f"Field '{field_id}' not found"))
            continue

        field_name = field.get("name")

        if not field_name:
            results.append(_skipped("2.5.3", issue_key, "Field has no /T name"))
            continue

        field_obj = _find_acroform_field(pdf, field_name)

        if field_obj is None:
            results.append(_skipped("2.5.3", issue_key, f"Field '{field_name}' not found in PDF"))
            continue

        _set_tooltip_everywhere(field_obj, visible_label)

        results.append(_fixed(
            "2.5.3",
            issue_key,
            f"Set /TU to visible label exactly: '{visible_label}'"
        ))

    return results