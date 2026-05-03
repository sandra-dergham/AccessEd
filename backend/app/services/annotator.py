import fitz
def _find_by_id(items: list[dict], id_value: str):
    return next((item for item in items if item.get("id") == id_value), None)


def _norm(value):
    return (value or "").strip().lower()

#some loactions don't include the id

def resolve_annotation_target(issue: dict, doc_json: dict):
    doc = doc_json.get("document", doc_json)
    loc = issue.get("location", {})

    # Direct visual objects
    if loc.get("span_id"):
        return _find_by_id(doc.get("text_spans", []), loc["span_id"])

    if loc.get("image_id"):
        return _find_by_id(
            doc.get("images", {}).get("occurrences", []),
            loc["image_id"]
        )

    if loc.get("widget_id"):
        return _find_by_id(doc.get("widgets", []), loc["widget_id"])

    if loc.get("graphic_id"):
        return _find_by_id(doc.get("graphics", []), loc["graphic_id"])

    if loc.get("link_id"):
        return _find_by_id(doc.get("links", []), loc["link_id"])

    # Field fallback: field_id -> acroform field -> widget bbox
    if loc.get("field_id"):
        fields = doc.get("interactivity", {}).get("acroform_fields", [])
        widgets = doc.get("widgets", [])

        field = _find_by_id(fields, loc["field_id"])
        if not field:
            return None

        field_name = _norm(field.get("name"))

        for widget in widgets:
            if _norm(widget.get("field_name")) == field_name:
                return widget

    return None

def rgb_to_pdf(rgb):
    return tuple(v / 255 for v in rgb)

SEVERITY_COLORS = {  # this matched the report builder color used 
    "high": rgb_to_pdf((185, 28, 28)),
    "medium": rgb_to_pdf((194, 65, 12)),
    "low": rgb_to_pdf((161, 98, 7)),
    "needs_review": rgb_to_pdf((37, 99, 235)),
}


def annotate_pdf(original_pdf_path: str, issues: list[dict], doc_json: dict, output_path: str):
    pdf = fitz.open(original_pdf_path)

    for issue in issues:
        severity = str(issue.get("severity", "")).lower()

        if severity not in SEVERITY_COLORS:
            continue

        target = resolve_annotation_target(issue, doc_json)
        if not target:
            continue

        page_index = target.get("page_index")
        bbox = target.get("bbox")

        if page_index is None or not bbox:
            continue

        page = pdf.load_page(page_index)
        rect = fitz.Rect(bbox)

        annot= page.add_rect_annot(rect)
        annot.set_colors(stroke=SEVERITY_COLORS[severity])
        annot.set_opacity(0.5)
        annot.set_border(width=1.2)
        annot.set_info(
            title=f"WCAG {issue.get('criterion')}",
            content=f"{issue.get('severity', '').upper()}\n{issue.get('issue')}\n{issue.get('recommendation', '')}"
        )
        annot.update()

    pdf.save(output_path)
    pdf.close()