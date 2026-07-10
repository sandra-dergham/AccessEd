import logging
import pikepdf
from ...utils.correction import (
    _filter_issues,
    _get_doc,
    _skipped,
    _fixed,
    _build_span_lookup,
)
logger = logging.getLogger(__name__)


def fix_3_1_1_language(
    pdf: pikepdf.Pdf,
    issues: list[dict],
    doc_json: dict,
    original_pdf_path: str,
) -> list[dict]:
    """WCAG 3.1.1 — language_of_page: set /Lang on the PDF catalog."""
    results = []
    targets = _filter_issues(issues, "3.1.1", "language_of_page")
    if not targets:
        return results

    lang = _get_doc(doc_json).get("inferred_language")
    if not lang:
        results.append(_skipped("3.1.1", "language_of_page",
                                "inferred_language not available"))
        return results

    pdf.Root["/Lang"] = pikepdf.String(lang)
    results.append(_fixed("3.1.1", "language_of_page",
                          f"Set document language to '{lang}'"))
    return results


def fix_2_4_2_title(
    pdf: pikepdf.Pdf,
    issues: list[dict],
    doc_json: dict,
    original_pdf_path: str,
) -> list[dict]:
    """WCAG 2.4.2 — page_titled: derive title from heading candidates and write to PDF metadata.

    Only uses heading candidates — the first non-empty span fallback was removed
    because it reliably produced bad titles (page numbers, dates, logo labels).
    """
    results = []
    targets = _filter_issues(issues, "2.4.2", "page_titled")
    if not targets:
        return results

    span_lookup        = _build_span_lookup(doc_json)
    heading_candidates = _get_doc(doc_json).get("heading_candidates", [])
    title              = None

    if heading_candidates:
        span = span_lookup.get(heading_candidates[0])
        if span:
            title = span.get("text", "").strip()

    # No reliable title source — skip rather than write a worse title
    if not title:
        results.append(_skipped("2.4.2", "page_titled",
                                "No heading candidates found — cannot derive a reliable title"))
        return results

    title = title[:80]
    pdf.docinfo["/Title"] = pikepdf.String(title)
    results.append(_fixed("2.4.2", "page_titled",
                          f"Set document title to '{title}'"))
    return results


def fix_2_4_1_and_2_4_5_bookmarks(
    pdf: pikepdf.Pdf,
    issues: list[dict],
    doc_json: dict,
    original_pdf_path: str,
) -> list[dict]:
    """WCAG 2.4.1 / 2.4.5 — bypass_blocks / multiple_ways: build bookmarks from heading candidates.

    Each outline item now carries a /Parent reference as required by the PDF spec,
    so readers that validate the outline tree do not silently drop the bookmarks.
    """
    results = []
    has_241 = bool(_filter_issues(issues, "2.4.1"))
    has_245 = bool(_filter_issues(issues, "2.4.5"))
    if not has_241 and not has_245:
        return results

    span_lookup        = _build_span_lookup(doc_json)
    heading_candidates = _get_doc(doc_json).get("heading_candidates", [])

    if not heading_candidates:
        for criterion in (["2.4.1"] if has_241 else []) + (["2.4.5"] if has_245 else []):
            results.append(_skipped(criterion, "bypass_blocks_multiple_ways",
                                    "No heading candidates found to build bookmarks"))
        return results

    headings = []
    for hid in heading_candidates:
        span = span_lookup.get(hid)
        if not span:
            continue
        text = span.get("text", "").strip()
        if not text:
            continue
        page_index = span.get("page_index", 0)
        font_size  = span.get("font", {}).get("size", 12)
        headings.append({"text": text, "page_index": page_index, "font_size": font_size})

    if not headings:
        for criterion in (["2.4.1"] if has_241 else []) + (["2.4.5"] if has_245 else []):
            results.append(_skipped(criterion, "bypass_blocks_multiple_ways",
                                    "Heading candidates had no usable text"))
        return results

    # Infer heading levels from font size
    unique_sizes   = sorted(set(h["font_size"] for h in headings), reverse=True)
    size_to_level  = {size: (i + 1) for i, size in enumerate(unique_sizes)}
    for h in headings:
        h["level"] = size_to_level[h["font_size"]]

    # Build pikepdf outline items (without /Parent yet — added after root is created)
    items = []
    for h in headings:
        page_ref = pdf.pages[h["page_index"]].obj
        item = pdf.make_indirect(pikepdf.Dictionary(
            Title=pikepdf.String(h["text"]),
            Dest=pikepdf.Array([page_ref, pikepdf.Name("/Fit")]),
            Count=pikepdf.Integer(0),
        ))
        items.append(item)

    # Chain items with /Next and /Prev
    for i, item in enumerate(items):
        if i > 0:
            item["/Prev"] = items[i - 1]
        if i < len(items) - 1:
            item["/Next"] = items[i + 1]

    # Build root /Outlines dictionary
    outline_root = pdf.make_indirect(pikepdf.Dictionary(
        Count=pikepdf.Integer(len(items)),
        First=items[0],
        Last=items[-1],
    ))

    # Link each item back to the root — required by PDF spec
    for item in items:
        item["/Parent"] = outline_root

    pdf.Root["/Outlines"] = outline_root

    for criterion in (["2.4.1"] if has_241 else []) + (["2.4.5"] if has_245 else []):
        results.append(_fixed(criterion, "bypass_blocks_multiple_ways",
                              f"Added {len(items)} bookmarks from heading candidates"))
    return results

def fix_2_4_4_link_purpose(
    pdf: pikepdf.Pdf,
    issues: list[dict],
    doc_json: dict,
    original_pdf_path: str,
) -> list[dict]:

    import urllib.parse
    from app.services.openai_client import get_openai_client

    results = []
    targets = _filter_issues(issues, "2.4.4", "link_purpose")
    if not targets:
        return results

    links       = _get_doc(doc_json).get("links", [])
    link_lookup = {lnk["id"]: lnk for lnk in links if lnk.get("id")}

    client = None

    for iss in targets:
        location   = iss.get("location", {})
        link_id    = location.get("link_id")
        page_index = location.get("page", 0)

        link = link_lookup.get(link_id)
        if not link:
            results.append(_skipped("2.4.4", link_id or "unknown",
                                    "Link not found in doc_json"))
            continue

        uri  = link.get("uri", "") or ""
        bbox = link.get("bbox", [])

        if not uri:
            results.append(_skipped("2.4.4", link_id,
                                    "Link has no URI — internal link, skipping"))
            continue

        label = None

        # Case A — URL has a readable path segment
        parsed = urllib.parse.urlparse(uri)
        path   = parsed.path.rstrip("/")
        if path and path != "/":
            segment = path.split("/")[-1]
            segment = segment.rsplit(".", 1)[0] if "." in segment else segment
            segment = segment.replace("-", " ").replace("_", " ").strip()
            if len(segment) > 3:
                label = segment.title()[:60]

        # Case B — opaque URL, clean up domain
        if not label:
            clean = uri.replace("https://", "").replace("http://", "").rstrip("/")
            if len(clean) <= 60:
                label = clean

        # Case C — use GPT-4o for context-aware label
        if not label or len(label) < 4:
            try:
                if client is None:
                    client = get_openai_client()

                context_spans = [
                    s.get("text", "") for s in
                    _get_doc(doc_json).get("text_spans", [])
                    if s.get("page_index") == page_index
                ]
                context = " ".join(context_spans)[:300]

                prompt = (
                    f"A PDF link points to: {uri}. "
                    f"Surrounding page text: {context}. "
                    f"Write a short descriptive label (max 60 characters) "
                    f"that clearly describes where this link goes. "
                    f"Return ONLY the label. No explanation."
                )

                response = client.chat.completions.create(
                    model="gpt-4o",
                    max_tokens=80,
                    messages=[{"role": "user", "content": prompt}],
                )
                label = response.choices[0].message.content.strip()[:60]
            except RuntimeError as exc:
                results.append(_skipped("2.4.4", link_id,
                                        f"OpenAI unavailable: {exc}"))
                continue
            except Exception as exc:
                logger.warning("GPT-4o failed for link %s: %s", link_id, exc)
                results.append(_skipped("2.4.4", link_id,
                                        f"GPT-4o call failed: {exc}"))
                continue

        if not label:
            results.append(_skipped("2.4.4", link_id, "Could not generate a label"))
            continue

        # Convert PyMuPDF coords to PDF annotation coords
        try:
            page_height = float(pdf.pages[page_index].mediabox[3])
        except Exception:
            results.append(_skipped("2.4.4", link_id,
                                    "Could not read page height for coordinate conversion"))
            continue

        if bbox and len(bbox) == 4:
            pdf_x0 = bbox[0]
            pdf_y0 = page_height - bbox[3]
        else:
            results.append(_skipped("2.4.4", link_id, "Link has no bbox"))
            continue

        # Write label to matching link annotation  ← INSIDE the for loop
        try:
            page_obj = pdf.pages[page_index]
            annots   = page_obj.get("/Annots") or []
            written  = False

            for annot_ref in annots:
                try:
                    annot = annot_ref if isinstance(annot_ref, pikepdf.Dictionary) else annot_ref.get_object()
                except Exception:
                    continue

                if str(annot.get("/Subtype", "")) != "/Link":
                    continue
                rect = annot.get("/Rect")
                if not rect:
                    continue
                r = [float(x) for x in rect]
                if abs(r[0] - pdf_x0) < 5 and abs(r[1] - pdf_y0) < 5:
                    annot["/Contents"] = pikepdf.String(label)
                    written = True
                    break

            if written:
                results.append(_fixed("2.4.4", link_id,
                                      f"Set link label to '{label}'"))
            else:
                results.append(_skipped("2.4.4", link_id,
                                        "Could not match annotation by bbox"))
        except Exception as exc:
            results.append(_skipped("2.4.4", link_id,
                                    f"Failed to write to PDF: {exc}"))

    return results