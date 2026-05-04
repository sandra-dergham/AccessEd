from __future__ import annotations

import logging
import re
import shutil
from typing import Any

import pikepdf

logger = logging.getLogger(__name__)


def _fixed(criterion: str, issue_id: str, detail: str) -> dict:
    return {"criterion": criterion, "issue": issue_id,
            "status": "fixed", "detail": detail}


def _skipped(criterion: str, issue_id: str, reason: str) -> dict:
    return {"criterion": criterion, "issue": issue_id,
            "status": "skipped", "detail": reason}


def _flagged(criterion: str, issue_id: str, reason: str) -> dict:
    return {"criterion": criterion, "issue": issue_id,
            "status": "flagged_manual", "detail": reason}


def _get_doc(doc_json: dict) -> dict:
    return doc_json.get("document", doc_json)


def _filter_issues(
    issues: list[dict],
    criterion: str,
    issue_key: str | None = None,
    severities: set[str] | None = None,
) -> list[dict]:
    exclude = {"pass", "not_applicable"}
    result = []
    for iss in issues:
        if iss.get("criterion") != criterion:
            continue
        if iss.get("severity", "") in exclude:
            continue
        if severities and iss.get("severity", "") not in severities:
            continue
        if issue_key and iss.get("issue") != issue_key:
            continue
        result.append(iss)
    return result


def _build_span_lookup(doc_json: dict) -> dict[str, dict]:
    spans = _get_doc(doc_json).get("text_spans", [])
    return {s["id"]: s for s in spans if s.get("id")}


def _find_acroform_field(
    pdf: pikepdf.Pdf,
    field_name_t: str,
) -> "pikepdf.Dictionary | None":
    try:
        acroform = pdf.Root.get("/AcroForm")
        if acroform is None:
            return None
        fields = acroform.get("/Fields")
        if fields is None:
            return None

        def walk(node: Any):
            if not isinstance(node, pikepdf.Dictionary):
                try:
                    node = node.get_object()
                except Exception:
                    return None
            t = node.get("/T")
            if t is not None and str(t) == field_name_t:
                return node
            kids = node.get("/Kids")
            if isinstance(kids, pikepdf.Array):
                for kid in kids:
                    found = walk(kid)
                    if found is not None:
                        return found
            return None

        for field_ref in fields:
            found = walk(field_ref)
            if found is not None:
                return found
    except Exception as exc:
        logger.debug("_find_acroform_field: %s", exc)
    return None


def _clean_field_name(raw: str) -> str:
    spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", raw)
    spaced = spaced.replace("_", " ").replace("-", " ")
    return spaced.strip().title()


def fix_1_1_1_image_alt_text(
    pdf: pikepdf.Pdf,
    issues: list[dict],
    doc_json: dict,
    original_pdf_path: str,
) -> list[dict]:
    """WCAG 1.1.1 — image_missing_text_alternative"""
    results = []
    targets = _filter_issues(issues, "1.1.1", "image_missing_text_alternative")
    for iss in targets:
        results.append(_skipped("1.1.1", iss.get("issue", ""),
                                "Not yet implemented - Jana"))
    return results


def fix_1_1_1_control_name(
    pdf: pikepdf.Pdf,
    issues: list[dict],
    doc_json: dict,
    original_pdf_path: str,
) -> list[dict]:
    """WCAG 1.1.1 — control_missing_name"""
    results = []
    targets = _filter_issues(issues, "1.1.1", "control_missing_name")
    for iss in targets:
        results.append(_skipped("1.1.1", iss.get("issue", ""),
                                "Not yet implemented - Jana"))
    return results


def fix_1_4_3_contrast(
    pdf: pikepdf.Pdf,
    issues: list[dict],
    doc_json: dict,
    original_pdf_path: str,
) -> list[dict]:
    """WCAG 1.4.3 — insufficient_text_contrast"""
    results = []
    targets = _filter_issues(issues, "1.4.3", "insufficient_text_contrast")
    for iss in targets:
        results.append(_skipped("1.4.3", iss.get("issue", ""),
                                "Not yet implemented - Jana"))
    return results


def fix_2_5_3_label_in_name(
    pdf: pikepdf.Pdf,
    issues: list[dict],
    doc_json: dict,
    original_pdf_path: str,
) -> list[dict]:
    """WCAG 2.5.3 — label_not_in_name"""
    results = []
    targets = _filter_issues(issues, "2.5.3", "label_not_in_name")
    for iss in targets:
        results.append(_skipped("2.5.3", iss.get("issue", ""),
                                "Not yet implemented - Jana"))
    return results


def fix_3_1_1_language(
    pdf: pikepdf.Pdf,
    issues: list[dict],
    doc_json: dict,
    original_pdf_path: str,
) -> list[dict]:
    """WCAG 3.1.1 — language_of_page"""
    results = []
    targets = _filter_issues(issues, "3.1.1", "language_of_page")
    if not targets:
        return results
    lang = _get_doc(doc_json).get("inferred_language")
    if not lang:
        results.append(_skipped("3.1.1", "language_of_page",
                                "inferred_language not available"))
        return results
    results.append(_skipped("3.1.1", "language_of_page",
                            "Not yet implemented - Hala"))
    return results


def fix_2_4_2_title(
    pdf: pikepdf.Pdf,
    issues: list[dict],
    doc_json: dict,
    original_pdf_path: str,
) -> list[dict]:
    """WCAG 2.4.2 — page_titled"""
    results = []
    targets = _filter_issues(issues, "2.4.2", "page_titled")
    if not targets:
        return results
    results.append(_skipped("2.4.2", "page_titled",
                            "Not yet implemented - Hala"))
    return results


def fix_2_4_1_and_2_4_5_bookmarks(
    pdf: pikepdf.Pdf,
    issues: list[dict],
    doc_json: dict,
    original_pdf_path: str,
) -> list[dict]:
    """WCAG 2.4.1 / 2.4.5 — bypass_blocks / multiple_ways"""
    results = []
    has_241 = bool(_filter_issues(issues, "2.4.1"))
    has_245 = bool(_filter_issues(issues, "2.4.5"))
    if not has_241 and not has_245:
        return results
    for criterion in (["2.4.1"] if has_241 else []) + (["2.4.5"] if has_245 else []):
        results.append(_skipped(criterion, "bypass_blocks_multiple_ways",
                                "Not yet implemented - Hala"))
    return results


def fix_2_4_4_link_purpose(
    pdf: pikepdf.Pdf,
    issues: list[dict],
    doc_json: dict,
    original_pdf_path: str,
) -> list[dict]:
    """WCAG 2.4.4 — link_purpose"""
    results = []
    targets = _filter_issues(issues, "2.4.4", "link_purpose")
    for iss in targets:
        results.append(_skipped("2.4.4", iss.get("issue", ""),
                                "Not yet implemented - Hala"))
    return results


def fix_3_3_2_form_tooltips(
    pdf: pikepdf.Pdf,
    issues: list[dict],
    doc_json: dict,
    original_pdf_path: str,
) -> list[dict]:
    """WCAG 3.3.2 — missing field tooltip /TU"""
    results = []
    targets = _filter_issues(issues, "3.3.2")
    if not targets:
        return results

    acroform_fields = (
        _get_doc(doc_json)
        .get("interactivity", {})
        .get("acroform_fields", [])
    )
    field_by_id = {f["id"]: f for f in acroform_fields if f.get("id")}

    TYPE_FALLBACK = {"Tx": "Text field", "Btn": "Button", "Ch": "Dropdown"}

    for iss in targets:
        loc = iss.get("location", {})
        field_id   = loc.get("field_id")
        field_name = loc.get("field_name")
        issue_key  = iss.get("issue", "")[:80]

        try:
            if field_name:
                label = _clean_field_name(field_name)
                field_obj = _find_acroform_field(pdf, field_name)
                if field_obj is None:
                    results.append(_skipped("3.3.2", issue_key,
                                            f"Field '{field_name}' not found in AcroForm"))
                    continue
                field_obj["/TU"] = pikepdf.String(label)
                results.append(_fixed("3.3.2", issue_key,
                                      f"Set /TU='{label}' on field '{field_name}'"))

            else:
                doc_field = field_by_id.get(field_id)
                if doc_field is None:
                    results.append(_skipped("3.3.2", issue_key,
                                            f"field_id '{field_id}' not found in doc_json"))
                    continue

                field_type = doc_field.get("type") or loc.get("field_type")
                label = TYPE_FALLBACK.get(field_type, "Form field")

                acroform = pdf.Root.get("/AcroForm")
                if acroform is None:
                    results.append(_skipped("3.3.2", issue_key, "No AcroForm in PDF"))
                    continue

                matched = False
                for field_ref in acroform.get("/Fields", []):
                    try:
                        obj = pdf.get_object(field_ref.objgen)
                        t_val  = obj.get("/T")
                        ft_val = str(obj.get("/FT", "")).lstrip("/")
                        tu_val = obj.get("/TU")

                        if t_val is None and ft_val == field_type and tu_val is None:
                            obj["/TU"] = pikepdf.String(label)
                            matched = True
                            break
                    except Exception:
                        continue

                if matched:
                    results.append(_fixed("3.3.2", issue_key,
                                          f"Set /TU='{label}' on unnamed {field_type} field"))
                else:
                    results.append(_skipped("3.3.2", issue_key,
                                            f"Could not locate unnamed field in AcroForm"))

        except Exception as exc:
            results.append(_skipped("3.3.2", issue_key, f"Error: {exc}"))

    return results


def fix_4_1_2_figure_alt(
    pdf: pikepdf.Pdf,
    issues: list[dict],
    doc_json: dict,
    original_pdf_path: str,
) -> list[dict]:
    """WCAG 4.1.2 — Figure missing /Alt (uses GPT-4o vision)"""
    import base64
    import fitz
    from app.services.openai_client import get_openai_client

    results = []
    targets = [
        iss for iss in issues
        if iss.get("criterion") == "4.1.2"
        and "Figure" in str(iss.get("issue", ""))
        and "/Alt" in str(iss.get("issue", ""))
        and iss.get("severity") not in {"pass", "not_applicable"}
    ]
    if not targets:
        return results

    doc_data    = _get_doc(doc_json)
    figures     = doc_data.get("structure", {}).get("figures", [])
    occurrences = doc_data.get("images", {}).get("occurrences", [])
    text_spans  = doc_data.get("text_spans", [])

    fig_by_id  = {f["id"]: f for f in figures if f.get("id")}
    occ_by_fig = {o["struct_figure_id"]: o for o in occurrences if o.get("struct_figure_id")}

    try:
        fitz_doc = fitz.open(original_pdf_path)
    except Exception as exc:
        for iss in targets:
            results.append(_skipped("4.1.2", "figure_missing_alt",
                                    f"Could not open PDF with fitz: {exc}"))
        return results

    try:
        client = get_openai_client()
    except RuntimeError as exc:
        for iss in targets:
            results.append(_skipped("4.1.2", "figure_missing_alt", str(exc)))
        fitz_doc.close()
        return results

    def _get_nearby_text(page_index: int, bbox: list) -> str:
        if not bbox or page_index is None:
            return ""
        y0 = bbox[1]
        nearby = [
            s["text"] for s in text_spans
            if s.get("page_index") == page_index
            and s.get("bbox")
            and abs(s["bbox"][1] - y0) < 50
        ]
        return " ".join(nearby[:10])

    def _extract_image_bytes_by_bbox(page_index: int, bbox: list) -> bytes | None:
        try:
            page = fitz_doc.load_page(page_index)
            img_list = page.get_images(full=True)
            if not img_list:
                return None

            if bbox:
                x0, y0, x1, y1 = bbox
                best_xref = None
                best_overlap = -1
                for img_info in page.get_image_info(xrefs=True):
                    ix0, iy0, ix1, iy1 = img_info["bbox"]
                    overlap = (
                        max(0, min(x1, ix1) - max(x0, ix0)) *
                        max(0, min(y1, iy1) - max(y0, iy0))
                    )
                    if overlap > best_overlap:
                        best_overlap = overlap
                        best_xref = img_info.get("xref")
                if best_xref:
                    return fitz_doc.extract_image(best_xref)["image"]

            return fitz_doc.extract_image(img_list[0][0])["image"] if img_list else None

        except Exception as exc:
            logger.debug("_extract_image_bytes_by_bbox: %s", exc)
            return None

    def _find_figure_node(struct_tree, target_mcids: list):
        try:
            if not isinstance(struct_tree, pikepdf.Dictionary):
                struct_tree = struct_tree.get_object()
        except Exception:
            return None

        s_type = str(struct_tree.get("/S", ""))
        if s_type == "/Figure":
            k = struct_tree.get("/K")
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
                if any(m in node_mcids for m in (target_mcids or [])):
                    return struct_tree

        kids = struct_tree.get("/K")
        if kids is None:
            return None
        if not isinstance(kids, pikepdf.Array):
            kids = [kids]
        for kid in kids:
            found = _find_figure_node(kid, target_mcids)
            if found is not None:
                return found
        return None

    for iss in targets:
        issue_key = "figure_missing_alt"
        try:
            target_fig = next(
                (f for f in figures if not f.get("alt") and not f.get("actual_text")),
                None
            )
            if target_fig is None:
                results.append(_skipped("4.1.2", issue_key,
                                        "No figure with missing /Alt found in doc_json"))
                continue

            fig_id     = target_fig["id"]
            mcids      = target_fig.get("mcids", [])
            occ        = occ_by_fig.get(fig_id)
            page_index = occ["page_index"] if occ else None
            bbox       = occ.get("bbox")   if occ else None

            if page_index is None:
                results.append(_skipped("4.1.2", issue_key,
                                        f"No page_index for figure '{fig_id}'"))
                continue

            img_bytes = _extract_image_bytes_by_bbox(page_index, bbox)
            if not img_bytes:
                results.append(_skipped("4.1.2", issue_key,
                                        f"Could not extract image bytes for figure '{fig_id}'"))
                continue

            nearby_text = _get_nearby_text(page_index, bbox)

            b64_image = base64.standard_b64encode(img_bytes).decode("utf-8")
            prompt = (
                f"Describe this image in one concise sentence (max 125 characters) "
                f"suitable as alt text for a PDF. Context: {nearby_text}. "
                f"Return ONLY the alt text. No explanation. No quotes."
            )
            response = client.chat.completions.create(
                model="gpt-4o",
                max_tokens=100,
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
            alt_text = response.choices[0].message.content.strip()

            if not alt_text or "cannot" in alt_text.lower() or len(alt_text) > 200:
                results.append(_skipped("4.1.2", issue_key,
                                        f"GPT-4o returned unusable alt text: '{alt_text}'"))
                continue

            struct_root = pdf.Root.get("/StructTreeRoot")
            if struct_root is None:
                results.append(_skipped("4.1.2", issue_key,
                                        "No /StructTreeRoot in PDF"))
                continue

            figure_node = _find_figure_node(struct_root, mcids)
            if figure_node is None:
                results.append(_skipped("4.1.2", issue_key,
                                        f"Figure node with MCIDs {mcids} not found in struct tree"))
                continue

            figure_node["/Alt"] = pikepdf.String(alt_text)
            results.append(_fixed("4.1.2", issue_key,
                                  f"Set /Alt='{alt_text[:60]}' on Figure node"))

        except Exception as exc:
            results.append(_skipped("4.1.2", issue_key, f"Error: {exc}"))

    fitz_doc.close()
    return results


def fix_4_1_2_checkbox_state(
    pdf: pikepdf.Pdf,
    issues: list[dict],
    doc_json: dict,
    original_pdf_path: str,
) -> list[dict]:
    """WCAG 4.1.2 — checkbox missing /AS appearance state"""
    results = []
    targets = [
        iss for iss in issues
        if iss.get("criterion") == "4.1.2"
        and "appearance state" in str(iss.get("issue", ""))
        and iss.get("severity") not in {"pass", "not_applicable"}
    ]
    if not targets:
        return results

    acroform_fields = (
        _get_doc(doc_json)
        .get("interactivity", {})
        .get("acroform_fields", [])
    )
    field_by_id = {f["id"]: f for f in acroform_fields if f.get("id")}

    for iss in targets:
        loc = iss.get("location", {})
        field_id  = loc.get("field_id")
        issue_key = "checkbox_missing_as_state"

        try:
            doc_field = field_by_id.get(field_id)
            if doc_field is None:
                results.append(_skipped("4.1.2", issue_key,
                                        f"field_id '{field_id}' not found in doc_json"))
                continue

            field_name = doc_field.get("name")
            if not field_name:
                results.append(_skipped("4.1.2", issue_key,
                                        f"Field has no /T value, cannot locate in AcroForm"))
                continue

            field_obj = _find_acroform_field(pdf, field_name)
            if field_obj is None:
                results.append(_skipped("4.1.2", issue_key,
                                        f"Field '{field_name}' not found in AcroForm"))
                continue

            field_obj["/AS"] = pikepdf.Name("/Off")
            results.append(_fixed("4.1.2", issue_key,
                                  f"Set /AS=/Off on field '{field_name}'"))

        except Exception as exc:
            results.append(_skipped("4.1.2", issue_key, f"Error: {exc}"))

    return results


def fix_2_1_1_tab_order(
    pdf: pikepdf.Pdf,
    issues: list[dict],
    doc_json: dict,
    original_pdf_path: str,
) -> list[dict]:
    """WCAG 2.1.1 — set /Tabs /S on pages with form fields"""
    results = []
    targets = _filter_issues(issues, "2.1.1")
    if not targets:
        return results

    try:
        acroform_fields = (
            _get_doc(doc_json)
            .get("interactivity", {})
            .get("acroform_fields", [])
        )
        page_indices = {f["page_index"] for f in acroform_fields if f.get("page_index") is not None}

        if not page_indices:
            results.append(_skipped("2.1.1", "no_tab_order", "No form fields found on any page"))
            return results

        for page_index in sorted(page_indices):
            if page_index < len(pdf.pages):
                pdf.pages[page_index]["/Tabs"] = pikepdf.Name("/S")

        results.append(_fixed(
            "2.1.1",
            "no_tab_order",
            f"Set /Tabs /S on {len(page_indices)} page(s): {sorted(page_indices)}"
        ))
    except Exception as exc:
        results.append(_skipped("2.1.1", "no_tab_order", f"Error: {exc}"))

    return results


FIXERS = [
    # Batch 2 — Hala (document-level fixes first)
    fix_3_1_1_language,
    fix_2_4_2_title,
    fix_2_4_1_and_2_4_5_bookmarks,
    fix_2_4_4_link_purpose,
    # Batch 1 — Jana
    fix_1_1_1_image_alt_text,
    fix_1_1_1_control_name,
    fix_1_4_3_contrast,
    fix_2_5_3_label_in_name,
    # Batch 3 — Sandra
    fix_3_3_2_form_tooltips,
    fix_4_1_2_figure_alt,
    fix_4_1_2_checkbox_state,
    fix_2_1_1_tab_order,
]


def apply_corrections(
    original_pdf_path: str,
    issues: list[dict],
    doc_json: dict,
    output_path: str,
) -> dict:
    shutil.copy2(original_pdf_path, output_path)
    all_results: list[dict] = []

    try:
        with pikepdf.open(output_path, allow_overwriting_input=True) as pdf:
            for fixer in FIXERS:
                try:
                    results = fixer(pdf, issues, doc_json, original_pdf_path)
                    all_results.extend(results)
                except Exception as exc:
                    logger.error("Fixer %s crashed: %s", fixer.__name__, exc)
                    all_results.append({
                        "criterion": "unknown", "issue": fixer.__name__,
                        "status": "error", "detail": str(exc),
                    })
            pdf.save(output_path)

    except Exception as exc:
        logger.error("apply_corrections could not open PDF: %s", exc)
        return {"status": "failed", "corrections": [],
                "fixed_count": 0, "skipped_count": 0,
                "flagged_count": 0, "error": str(exc)}

    fixed   = sum(1 for r in all_results if r.get("status") == "fixed")
    skipped = sum(1 for r in all_results if r.get("status") == "skipped")
    flagged = sum(1 for r in all_results if r.get("status") == "flagged_manual")

    return {
        "status":        "success" if fixed > 0 else "partial",
        "corrections":   all_results,
        "fixed_count":   fixed,
        "skipped_count": skipped,
        "flagged_count": flagged,
    }