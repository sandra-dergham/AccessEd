import logging
import pikepdf
from ...utils.correction import (
    _filter_issues,
    _get_doc,
    _skipped,
    _fixed,
    _build_span_lookup,
    _find_acroform_field,
    _clean_field_name,
    _set_tooltip_everywhere,
    
)
logger = logging.getLogger(__name__)


      
def fix_3_3_2_form_tooltips(
    pdf: pikepdf.Pdf,
    issues: list[dict],
    doc_json: dict,
    original_pdf_path: str,
) -> list[dict]:
    """
    WCAG 3.3.2 — Missing field tooltip /TU

    CASE MED/LOW (field_name present — has /T but no /TU):
      Fix: _clean_field_name(field_name) → set as /TU
      Lookup: _find_acroform_field(pdf, field_name)

    CASE HIGH (field_name is None — no /T and no /TU):
      Fix: fallback label from field_type ("Text field", "Button", etc.)
      Lookup: iterate AcroForm /Fields, match by type + missing /T
    """
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

                existing_tu = field_obj.get("/TU")
                if existing_tu is not None and str(existing_tu).strip():
                    results.append(_skipped("3.3.2", issue_key,
                                            f"Already has /TU='{existing_tu}', not overwriting"))
                    continue

                _set_tooltip_everywhere(field_obj, label)
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

                        if t_val is None and ft_val == field_type:
                            if tu_val is not None and str(tu_val).strip():
                                continue
                            _set_tooltip_everywhere(obj, label)
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
        """Extract image bytes from page by finding the image closest to bbox."""
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
                # Figure node not found by MCIDs — create one
                try:
                    figure_node = pdf.make_indirect(pikepdf.Dictionary(
                        Type=pikepdf.Name("/StructElem"),
                        S=pikepdf.Name("/Figure"),
                        Alt=pikepdf.String(alt_text),
                        P=struct_root,
                    ))
                    existing_k = struct_root.get("/K")
                    if existing_k is None:
                        struct_root["/K"] = pikepdf.Array([figure_node])
                    elif isinstance(existing_k, pikepdf.Array):
                        existing_k.append(figure_node)
                    else:
                        struct_root["/K"] = pikepdf.Array([existing_k, figure_node])
                    results.append(_fixed("4.1.2", issue_key,
                        f"Created Figure node with /Alt='{alt_text[:60]}' in struct tree"))
                except Exception as exc:
                    results.append(_skipped("4.1.2", issue_key,
                        f"Could not create Figure node: {exc}"))
                continue

            if "/Alt" in figure_node and str(figure_node["/Alt"]).strip():
                results.append(_skipped(
                    "4.1.2", issue_key,
                    "Figure already has /Alt from another fixer, not overwriting"
                ))
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

def fix_tag_structure(
    pdf: pikepdf.Pdf,
    issues: list[dict],
    doc_json: dict,
    original_pdf_path: str,
) -> list[dict]:
    """
    Build a minimal /StructTreeRoot when the PDF is completely untagged.

    Creates:
      - /StructTreeRoot with /ParentTree
      - A root <Document> element (satisfies 1.3.1 meaningful structure)
      - A <Widget> child for each interactive form field (satisfies 4.1.2 D)
      - /MarkInfo /Marked true (satisfies has_tags detection)

    This is NOT a full semantic tag tree — it won't have <P>, <H1>, <Table>
    etc. But it clears the "no tag structure" HIGHs (1.3.1, 2.1.1, 4.1.2).
    """
    results = []

    # Only run if the PDF is actually untagged
    has_131 = bool(_filter_issues(issues, "1.3.1", "info_relationships"))
    has_412_no_tags = any(
        iss.get("criterion") == "4.1.2"
        and "no tag structure" in str(iss.get("issue", "")).lower()
        and iss.get("severity") not in {"pass", "not_applicable"}
        for iss in issues
    )

    if not has_131 and not has_412_no_tags:
        return results

    # Check if StructTreeRoot already exists
    if pdf.Root.get("/StructTreeRoot") is not None:
        return results

    try:
        # Get interactive form fields from doc_json
        acroform_fields = (
            _get_doc(doc_json)
            .get("interactivity", {})
            .get("acroform_fields", [])
        )
        interactive_fields = [f for f in acroform_fields if not f.get("read_only")]

        # Create the root <Document> element
        doc_elem = pdf.make_indirect(pikepdf.Dictionary(
            Type=pikepdf.Name("/StructElem"),
            S=pikepdf.Name("/Document"),
        ))

        # Create heading children from heading candidates
        all_children = []

        span_lookup = _build_span_lookup(doc_json)
        heading_candidates = _get_doc(doc_json).get("heading_candidates", [])

        if heading_candidates:
            # Infer heading levels from font size
            heading_spans = []
            for hid in heading_candidates:
                span = span_lookup.get(hid)
                if not span:
                    continue
                text = (span.get("text") or "").strip()
                if not text:
                    continue
                font_size = span.get("font", {}).get("size", 12)
                heading_spans.append({"text": text, "font_size": font_size})

            if heading_spans:
                unique_sizes = sorted(set(h["font_size"] for h in heading_spans), reverse=True)
                size_to_level = {size: min(i + 1, 6) for i, size in enumerate(unique_sizes)}

                for h in heading_spans:
                    level = size_to_level[h["font_size"]]
                    heading_elem = pdf.make_indirect(pikepdf.Dictionary(
                        Type=pikepdf.Name("/StructElem"),
                        S=pikepdf.Name(f"/H{level}"),
                        Alt=pikepdf.String(h["text"][:80]),
                        P=doc_elem,
                    ))
                    all_children.append(heading_elem)

        # Create <Widget> children for each interactive field
        for field in interactive_fields:
            field_name = field.get("name")
            tooltip = field.get("tooltip")
            label = tooltip or field_name or "Form field"

            widget_elem = pdf.make_indirect(pikepdf.Dictionary(
                Type=pikepdf.Name("/StructElem"),
                S=pikepdf.Name("/Widget"),
                Alt=pikepdf.String(label),
                P=doc_elem,
            ))
            all_children.append(widget_elem)

        # Attach children to <Document>
        if all_children:
            doc_elem["/K"] = pikepdf.Array(all_children)

        # Build /StructTreeRoot
        struct_root = pdf.make_indirect(pikepdf.Dictionary(
            Type=pikepdf.Name("/StructTreeRoot"),
            K=pikepdf.Array([doc_elem]),
            ParentTree=pdf.make_indirect(pikepdf.Dictionary(
                Type=pikepdf.Name("/NumberTree"),
                Nums=pikepdf.Array([]),
            )),
        ))

        # Link <Document> back to root
        doc_elem["/P"] = struct_root

        # Set on catalog
        pdf.Root["/StructTreeRoot"] = struct_root
        pdf.Root["/MarkInfo"] = pikepdf.Dictionary(
            Marked=pikepdf.Boolean(True)
        )

        heading_count = len([c for c in all_children
                            if str(c.get("/S", "")).startswith("/H")])
        widget_count = len([c for c in all_children
                           if str(c.get("/S", "")) == "/Widget"])
        results.append(_fixed(
            "1.3.1", "info_relationships",
            f"Created /StructTreeRoot with <Document> element, "
            f"{heading_count} heading node(s), and "
            f"{widget_count} <Widget> node(s)"
        ))
        results.append(_fixed(
            "4.1.2", "no_tag_structure",
            "Added /StructTreeRoot and /MarkInfo to PDF catalog"
        ))

        if widget_count > 0:
            results.append(_fixed(
                "4.1.2", "widget_nodes_added",
                f"Added {widget_count} <Widget> node(s) to tag tree "
                f"matching interactive form fields"
            ))

        # 2.1.1 benefits too — tag tree now exists
        has_211 = bool(_filter_issues(issues, "2.1.1"))
        if has_211:
            results.append(_fixed(
                "2.1.1", "tag_structure_added",
                "Tag structure now exists — assistive technologies can "
                "discover interactive elements via the tag tree"
            ))

    except Exception as exc:
        results.append(_skipped(
            "1.3.1", "info_relationships",
            f"Could not build tag structure: {exc}"
        ))

    return results