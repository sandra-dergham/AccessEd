"""
corrector.py
AccessEd — PDF Accessibility Correction Engine

Place this file at:
    backend/app/services/corrector.py

─────────────────────────────────────────────────────────────
OWNERSHIP
─────────────────────────────────────────────────────────────
  Jana   → Batch 1  (1.1.1 alt text, 1.1.1 control name, 1.4.3 contrast, 2.5.3)
  Hala   → Batch 2  (3.1.1 language, 2.4.2 title, 2.4.1/2.4.5 bookmarks, 2.4.4 links)
  Sandra → Batch 3  (3.3.2 tooltips, 4.1.2 figure /Alt, 4.1.2 checkbox /AS, 2.1.1 tab order)

─────────────────────────────────────────────────────────────
HOW TO ADD YOUR FIX
─────────────────────────────────────────────────────────────
  1. Find your function (search your name in this file).
  2. Read the docstring — it tells you exactly what data you
     have and what pikepdf calls to make.
  3. Replace the stub body with your real implementation.
  4. When a fix succeeds  → return _fixed(...)
     When data is missing → return _skipped(...)
     When nothing can be done → return _flagged(...)
  5. DO NOT touch apply_corrections(), FIXERS, or shared helpers.
  6. Pull before you push. Tell the others when you merge to main.

─────────────────────────────────────────────────────────────
RULES THAT ARE FLAGGED ONLY — no fix attempted
─────────────────────────────────────────────────────────────
  1.2.x  media rules — out of scope per problem statement
  1.3.1  missing struct tree — too risky to add retroactively
  1.3.2  reading order — requires full re-tagging
  1.4.1  color-only cues — requires visual redesign
  1.4.4  resize risk — PDF layout is fixed
  1.4.5  images of text — cannot convert image to real text
  1.4.10 reflow — fixed PDF layout cannot adapt
  1.4.11 non-text contrast — unsafe to modify graphic streams
  1.4.12 text spacing — modifying spacing breaks layout
  2.4.6  headings in struct tree — retagging risks corruption
  2.2.x  timing rules — needs_review, live testing only
  2.3.1  flash — needs_review, specialist tooling required
  3.1.2  language of parts — structural changes needed
  3.3.1/3/4 error rules — live form testing only
  4.1.2D widget count < field count — deep re-tagging needed
"""

from __future__ import annotations

import logging
import re
import shutil
from typing import Any

import pikepdf
from .wcag.helper_function_b1 import contrast_ratio,_find_accessible_color

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# SHARED HELPERS — DO NOT MODIFY
# ═══════════════════════════════════════════════════════════════

def _fixed(criterion: str, issue_id: str, detail: str) -> dict:
    """Return this when your fix was applied successfully."""
    return {"criterion": criterion, "issue": issue_id,
            "status": "fixed", "detail": detail}


def _skipped(criterion: str, issue_id: str, reason: str) -> dict:
    """Return this when required data is missing so fix cannot run."""
    return {"criterion": criterion, "issue": issue_id,
            "status": "skipped", "detail": reason}


def _flagged(criterion: str, issue_id: str, reason: str) -> dict:
    """Return this for violations that cannot be auto-fixed at all."""
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
    """
    Return issues matching criterion, always excluding pass/not_applicable.
    Optionally filter by exact issue string and/or a set of severities.
    """
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
    """Return {span_id: span_dict} for fast lookups into text_spans."""
    spans = _get_doc(doc_json).get("text_spans", [])
    return {s["id"]: s for s in spans if s.get("id")}


def _find_acroform_field(
    pdf: pikepdf.Pdf,
    field_name_t: str,
) -> "pikepdf.Dictionary | None":
    """
    Walk the AcroForm /Fields tree and return the pikepdf field object
    whose /T value equals field_name_t.
    Returns None if AcroForm is absent or field is not found.
    """
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
    """
    Turn an auto-generated field name into a human-readable label.
      "first_name" -> "First Name"
      "emailAddr"  -> "Email Addr"
    """
    spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", raw)
    spaced = spaced.replace("_", " ").replace("-", " ")
    return spaced.strip().title()


# ═══════════════════════════════════════════════════════════════
# BATCH 1 FIXES — Jana
# ═══════════════════════════════════════════════════════════════

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
    import logging
    import fitz
    from app.services.openai_client import get_openai_client

    logger = logging.getLogger(__name__)

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

    def _bbox_center(bbox: list[float]) -> tuple[float, float]:
        return ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)

    def _extract_image_bytes(page_index: int, occ_bbox: list[float]) -> bytes | None:
        """
        Finds the image on the page whose visual rectangle overlaps the occurrence bbox.
        """
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

            base_image = fitz_doc.extract_image(best_xref)
            return base_image.get("image")

        except Exception as exc:
            logger.debug("_extract_image_bytes failed: %s", exc)
            return None

    def _nearby_text(page_index: int, bbox: list[float]) -> str:
        """
        Collects text close to the image bbox for better AI context.
        """
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

            span_rect = fitz.Rect(span_bbox)

            if expanded.intersects(span_rect):
                nearby.append(text)

        return " ".join(nearby)[:500]

    def _iter_struct_elems(obj):
        """
        Recursively walks the structure tree.
        """
        try:
            if isinstance(obj, pikepdf.Dictionary):
                if obj.get("/Type") == pikepdf.Name("/StructElem"):
                    yield obj

                kids = obj.get("/K")
                if kids is not None:
                    yield from _iter_struct_elems(kids)

            elif isinstance(obj, pikepdf.Array):
                for item in obj:
                    yield from _iter_struct_elems(item)

        except Exception:
            return

def _find_figure_node(struct_figure_id: str):
    """
    Finds a Figure structure node by matching struct_figure_id
    against doc_json figures, then walking struct tree by MCIDs.
    """
    try:
        # Get MCIDs for this figure from doc_json
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

        # /K might be a direct integer MCID — not a child node, skip iteration
        if isinstance(kids, pikepdf.Array):
            for kid in kids:
                try:
                    # skip plain integers — they are MCIDs, not child nodes
                    if isinstance(kid, (int, float)):
                        continue
                    found = walk(kid)
                    if found is not None:
                        return found
                except Exception:
                    continue
        # if /K is not an array, it's a direct MCID integer — nothing to recurse into

    except Exception:
        pass
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
                messages=[
                    {
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
                    }
                ],
            )

            alt_text = response.choices[0].message.content.strip().strip('"').strip("'")

            bad_phrases = [
                "i cannot",
                "i can't",
                "sorry",
                "unable to",
                "cannot determine",
            ]

            if not alt_text:
                return None

            if len(alt_text) > 200:
                return None

            if "\n" in alt_text:
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

            if not struct_figure_id:
                results.append(_skipped(
                    "1.1.1",
                    issue_key,
                    "No linked Figure structure node found; creating new tags should be handled separately"
                ))
                continue

            figure_node = _find_figure_node(struct_figure_id)

            if figure_node is None:
                results.append(_skipped("1.1.1", issue_key, f"Figure node '{struct_figure_id}' not found"))
                continue

            figure_node["/Alt"] = pikepdf.String(alt_text)

            results.append(_fixed(
                "1.1.1",
                issue_key,
                f"Set image /Alt='{alt_text}' via GPT-4o vision"
            ))

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
            field_obj  = _find_acroform_field(pdf, field_name) if field_name else None
            if field_obj is None:
                results.append(_skipped("1.1.1", issue_key, "Field not found in PDF"))
                continue

            # ── screenshot around the widget for visual context ──────────
            page_index = widget.get("page_index")
            bbox       = widget.get("bbox")
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
            field_obj["/TU"] = pikepdf.String(accessible_name)
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


    for iss in targets:
        try:
            loc     = iss.get("location", {})
            span_id = loc.get("span_id")
            span    = span_lookup.get(span_id)

            if span is None:
                results.append(_skipped("1.4.3", issue_key, f"Span '{span_id}' not found"))
                continue

            fg_rgb = span.get("color", {}).get("fill_rgb") or span.get("fill_rgb")
            bg_rgb = span.get("background_estimate", {}).get("bg_rgb") or span.get("bg_rgb")
            large_text = span.get("contrast", {}).get("large_text_assumed", False)

            if not fg_rgb or not bg_rgb:
                results.append(_skipped("1.4.3", issue_key, "Missing fg or bg RGB"))
                continue

            target_ratio  = 3.0 if large_text else 4.5
            current_ratio = contrast_ratio(fg_rgb, bg_rgb)

            if current_ratio >= target_ratio:
                results.append(_fixed("1.4.3", issue_key, f"Already passes: {current_ratio:.2f}:1"))
                continue

            new_fg_rgb = _find_accessible_color(fg_rgb, bg_rgb, target_ratio)
            if new_fg_rgb is None:
                results.append(_skipped("1.4.3", issue_key, "Could not compute accessible color"))
                continue

            # ── attempt content stream edit ──────────────────────────────
            page_index = span.get("page_index")
            success    = _try_recolor_in_stream(pdf, page_index, fg_rgb, new_fg_rgb)

            new_ratio = contrast_ratio(new_fg_rgb, bg_rgb)

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


def _try_recolor_in_stream(
    pdf: pikepdf.Pdf,
    page_index: int,
    old_rgb: list[int],
    new_rgb: list[int],
) -> bool:
    """
    Try to replace the detected old text fill color in the page content stream.

    General rule:
    - Uses old_rgb from the detector, not hardcoded demo colors.
    - Replaces only non-stroking RGB text/color operator: rg
    - Only edits if exactly one safe match is found.
    - Otherwise returns False so the issue is flagged/manual.
    """
    try:
        import re
        import zlib

        page = pdf.pages[page_index]
        contents = page.get("/Contents")

        if contents is None:
            return False

        def to_pdf_float(v: int) -> float:
            return round(v / 255.0, 4)

        old_vals = [to_pdf_float(v) for v in old_rgb]
        new_vals = [to_pdf_float(v) for v in new_rgb]

        new_op = (
            f"{new_vals[0]:.4f} {new_vals[1]:.4f} {new_vals[2]:.4f} rg"
            .rstrip("0")
            .encode()
        )

        # General numeric matcher:
        # Finds: number number number rg
        color_pattern = re.compile(
            rb"(?P<r>[0-9]*\.?[0-9]+)\s+"
            rb"(?P<g>[0-9]*\.?[0-9]+)\s+"
            rb"(?P<b>[0-9]*\.?[0-9]+)\s+rg"
        )

        def close(a: float, b: float, tolerance: float = 0.01) -> bool:
            return abs(a - b) <= tolerance

        def read_stream(stream):
            raw = stream.read_raw_bytes()

            try:
                data = zlib.decompress(raw)
                return data, True
            except Exception:
                return raw, False

        def write_stream(stream, data: bytes, was_compressed: bool):
            if was_compressed:
                stream.write(
                    zlib.compress(data),
                    filter=pikepdf.Name("/FlateDecode")
                )
            else:
                stream.write(data)

        def try_replace(stream) -> bool:
            data, was_compressed = read_stream(stream)

            matches = []

            for match in color_pattern.finditer(data):
                try:
                    r = float(match.group("r"))
                    g = float(match.group("g"))
                    b = float(match.group("b"))
                except Exception:
                    continue

                if (
                    close(r, old_vals[0])
                    and close(g, old_vals[1])
                    and close(b, old_vals[2])
                ):
                    matches.append(match)

            # Safety rule:
            # If the same color appears multiple times, we don't know if all
            # occurrences belong to the target span.
            if len(matches) != 1:
                return False

            match = matches[0]

            new_data = (
                data[:match.start()]
                + new_op
                + data[match.end():]
            )

            write_stream(stream, new_data, was_compressed)
            return True

        if isinstance(contents, pikepdf.Stream):
            return try_replace(contents)

        if isinstance(contents, pikepdf.Array):
            changed = False

            for item in contents:
                try:
                    stream = item.get_object()
                except Exception:
                    stream = item

                if not isinstance(stream, pikepdf.Stream):
                    continue

                if try_replace(stream):
                    changed = True
                    break

            return changed

        return False

    except Exception as exc:
        logger.debug("_try_recolor_in_stream failed: %s", exc)
        return False

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
            field_name = widget.get("field_name")
            field_obj  = _find_acroform_field(pdf, field_name) if field_name else None

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
                # force viewer redraw
                if "/AP" in field_obj:
                    del field_obj["/AP"]

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

        field_obj["/TU"] = pikepdf.String(visible_label)

        results.append(_fixed(
            "2.5.3",
            issue_key,
            f"Set /TU to visible label exactly: '{visible_label}'"
        ))

    return results

# ═══════════════════════════════════════════════════════════════
# BATCH 2 FIXES — Hala
# ═══════════════════════════════════════════════════════════════

def fix_3_1_1_language(
    pdf: pikepdf.Pdf,
    issues: list[dict],
    doc_json: dict,
    original_pdf_path: str,
) -> list[dict]:
    """
    WCAG 3.1.1 - language_of_page (severity: medium)
    Method: Pure code 

    DATA AVAILABLE:
      doc_json["document"]["inferred_language"]
        -> BCP-47 code e.g. "en", "ar", "fr", "en-US"
        -> computed by infer_document_language() in parsing.py
        -> may be None if detection failed

    FIX STEPS:
      1. Get inferred_language from doc_json
      2. If None -> _skipped
      3. Write: pdf.Root["/Lang"] = pikepdf.String(inferred_language)
      4. Return _fixed(...) on success

    NOTE: No loop needed. Document-level fix. Apply once.
    This is 5 lines of code. Do this first on Day 1.
    """
    # Hala implements here
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
    """
    WCAG 2.4.2 - page_titled (severity: medium)
    Method: Pure code

    DATA AVAILABLE:
      doc_json["document"]["heading_candidates"]
        -> list of span IDs (strings) — IDs only, not the text itself
        -> must look up text in text_spans

      doc_json["document"]["text_spans"]
        each span has: id, text, page_index, font["size"]

    FIX STEPS:
      1. Check issue exists for 2.4.2 page_titled
      2. span_lookup = _build_span_lookup(doc_json)
      3. heading_candidates = _get_doc(doc_json).get("heading_candidates", [])
      4. If heading_candidates not empty:
           title = span_lookup[heading_candidates[0]]["text"]
         Else fallback:
           title = first non-empty span["text"] in text_spans
      5. Truncate to 80 chars if needed: title = title[:80]
      6. Write: pdf.docinfo["/Title"] = pikepdf.String(title)
      7. Return _fixed(...) on success
    """
    results = []
    targets = _filter_issues(issues, "2.4.2", "page_titled")
    if not targets:
        return results

    # Step 1 — build span lookup
    span_lookup = _build_span_lookup(doc_json)

    # Step 2 — try heading candidates first
    title = None
    heading_candidates = _get_doc(doc_json).get("heading_candidates", [])

    if heading_candidates:
        span = span_lookup.get(heading_candidates[0])
        if span:
            title = span.get("text", "").strip()

    # Step 3 — fallback to first non-empty text span
    if not title:
        for span in _get_doc(doc_json).get("text_spans", []):
            text = span.get("text", "").strip()
            if text:
                title = text
                break

    # Step 4 — nothing found at all
    if not title:
        results.append(_skipped("2.4.2", "page_titled",
                                "Could not derive title from document content"))
        return results

    # Step 5 — truncate and write
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
    """
    WCAG 2.4.1 / 2.4.5 - bypass_blocks / multiple_ways
    Method: Pure code
    ...
    """
    results = []
    has_241 = bool(_filter_issues(issues, "2.4.1"))
    has_245 = bool(_filter_issues(issues, "2.4.5"))
    if not has_241 and not has_245:
        return results

    # Step 1 — build span lookup
    span_lookup = _build_span_lookup(doc_json)
    heading_candidates = _get_doc(doc_json).get("heading_candidates", [])

    if not heading_candidates:
        for criterion in (["2.4.1"] if has_241 else []) + (["2.4.5"] if has_245 else []):
            results.append(_skipped(criterion, "bypass_blocks_multiple_ways",
                                    "No heading candidates found to build bookmarks"))
        return results

    # Step 2 — collect headings with text, page, font size
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
        headings.append({
            "text":       text,
            "page_index": page_index,
            "font_size":  font_size,
        })

    if not headings:
        for criterion in (["2.4.1"] if has_241 else []) + (["2.4.5"] if has_245 else []):
            results.append(_skipped(criterion, "bypass_blocks_multiple_ways",
                                    "Heading candidates had no usable text"))
        return results

    # Step 3 — infer heading levels from font size
    # largest font = level 1, next unique size = level 2, etc.
    unique_sizes = sorted(set(h["font_size"] for h in headings), reverse=True)
    size_to_level = {size: (i + 1) for i, size in enumerate(unique_sizes)}
    for h in headings:
        h["level"] = size_to_level[h["font_size"]]

    # Step 4 — build pikepdf outline items
    # each item needs to be an indirect object so we can reference it
    items = []
    for h in headings:
        page_ref = pdf.pages[h["page_index"]].obj
        item = pdf.make_indirect(pikepdf.Dictionary(
            Title=pikepdf.String(h["text"]),
            Dest=pikepdf.Array([page_ref, pikepdf.Name("/Fit")]),
            Count=pikepdf.Integer(0),
        ))
        items.append(item)

    # Step 5 — chain items with /Next and /Prev
    for i, item in enumerate(items):
        if i > 0:
            item["/Prev"] = items[i - 1]
        if i < len(items) - 1:
            item["/Next"] = items[i + 1]

    # Step 6 — build root /Outlines dictionary
    outline_root = pdf.make_indirect(pikepdf.Dictionary(
        Count=pikepdf.Integer(len(items)),
        First=items[0],
        Last=items[-1],
    ))

    # Step 7 — write to PDF
    pdf.Root["/Outlines"] = outline_root

    # Step 8 — report success for whichever criteria fired
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
    """
    WCAG 2.4.4 - link_purpose (severity: medium)
    Method: Pure code (cases A+B) + GPT-4o (case C)
    """
    import urllib.parse

    results = []
    targets = _filter_issues(issues, "2.4.4", "link_purpose")
    if not targets:
        return results

    # Build link lookup from doc_json
    links = _get_doc(doc_json).get("links", [])
    link_lookup = {lnk["id"]: lnk for lnk in links if lnk.get("id")}

    # Build span lookup for surrounding text context
    span_lookup = _build_span_lookup(doc_json)

    for iss in targets:
        location   = iss.get("location", {})
        link_id    = location.get("link_id")
        page_index = location.get("page", 0)

        # Step 1 — find the link in doc_json
        link = link_lookup.get(link_id)
        if not link:
            results.append(_skipped("2.4.4", link_id or "unknown",
                                    "Link not found in doc_json"))
            continue

        uri       = link.get("uri", "") or ""
        bbox      = link.get("bbox", [])
        link_text = link.get("text", "") or ""

        if not uri:
            results.append(_skipped("2.4.4", link_id,
                                    "Link has no URI — internal link, skipping"))
            continue

        # Step 2 — determine case and generate label
        label = None

        # Case A — link text is a raw URL, clean it up
        if uri.lower() in link_text.lower() or link_text.startswith("http"):
            clean = uri.replace("https://", "").replace("http://", "")
            clean = clean.rstrip("/")
            label = clean[:60]

        # Case B — vague text but URL has a readable path segment
        if not label:
            parsed = urllib.parse.urlparse(uri)
            path   = parsed.path.rstrip("/")
            if path and path != "/":
                segment = path.split("/")[-1]
                # remove file extension
                segment = segment.rsplit(".", 1)[0] if "." in segment else segment
                # clean underscores/hyphens
                segment = segment.replace("-", " ").replace("_", " ").strip()
                if len(segment) > 3:  # meaningful segment
                    label = segment.title()[:60]

        # Case C — opaque URL, use GPT-4o
        if not label:
            try:
                import httpx, base64

                # collect surrounding text as context
                context_spans = [
                    s.get("text", "") for s in
                    _get_doc(doc_json).get("text_spans", [])
                    if s.get("page_index") == page_index
                ]
                context = " ".join(context_spans)[:300]

                prompt = (
                    f"A PDF link has vague text: \"{link_text}\". "
                    f"The link URL is: {uri}. "
                    f"Surrounding text: {context}. "
                    f"Write a short descriptive label (max 60 characters) "
                    f"that clearly describes where this link goes. "
                    f"Return ONLY the label. No explanation."
                )

                response = httpx.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {_get_openai_key()}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "gpt-4o",
                        "max_tokens": 80,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                    timeout=30,
                )
                label = response.json()["choices"][0]["message"]["content"].strip()
                label = label[:60]
            except Exception as exc:
                logger.warning("GPT-4o failed for link %s: %s", link_id, exc)
                results.append(_skipped("2.4.4", link_id,
                                        f"GPT-4o call failed: {exc}"))
                continue

        if not label:
            results.append(_skipped("2.4.4", link_id,
                                    "Could not generate a label"))
            continue

        # Step 3 — write label as /Contents on the link annotation in the PDF
        try:
            page_obj = pdf.pages[page_index]
            annots   = page_obj.get("/Annots", [])
            written  = False

            for annot_ref in annots:
                annot = annot_ref.get_object()
                if str(annot.get("/Subtype", "")) != "/Link":
                    continue
                rect = annot.get("/Rect")
                if rect and bbox:
                    # match annotation by bbox proximity
                    r = [float(x) for x in rect]
                    if (abs(r[0] - bbox[0]) < 5 and abs(r[1] - bbox[1]) < 5):
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

# ═══════════════════════════════════════════════════════════════
# BATCH 3 FIXES — Sandra
# ═══════════════════════════════════════════════════════════════

def fix_3_3_2_form_tooltips(
    pdf: pikepdf.Pdf,
    issues: list[dict],
    doc_json: dict,
    original_pdf_path: str,
) -> list[dict]:
    """
    WCAG 3.3.2 - Missing field tooltip /TU (severity: high/medium/low)
    Owner: Sandra | Method: Pure code

    TWO SUB-CASES — detect by checking location["field_name"]:

    CASE HIGH (field_name is None — no /T and no /TU):
      location has: field_id, field_type
      Fix: use field_type as generic fallback:
           "Tx"  -> "Text field"
           "Btn" -> "Button"
           "Ch"  -> "Dropdown"
           None/other -> "Form field"
      Finding field: you have no /T to search by, so iterate AcroForm fields
      by index or match by page_index + type from doc_json acroform_fields

    CASE MED/LOW (field_name is present — has /T but no /TU):
      location has: field_id, field_name (the /T value)
      Fix: _clean_field_name(location["field_name"])
      Finding field: _find_acroform_field(pdf, location["field_name"])

    WRITE FIX (both cases):
      field_obj["/TU"] = pikepdf.String(label)

    DATA AVAILABLE:
      doc_json["document"]["interactivity"]["acroform_fields"]
        each field: id, name (/T), tooltip (/TU), type, page_index
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
                # CASE MED/LOW: has /T, just missing /TU
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
                # CASE HIGH: no /T at all — match by type, no /TU already set
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
                # Find image whose bbox best overlaps with the figure bbox
                x0, y0, x1, y1 = bbox
                best_xref = None
                best_overlap = -1
                for img_info in page.get_image_info(xrefs=True):
                    ix0 = img_info["bbox"][0]
                    iy0 = img_info["bbox"][1]
                    ix1 = img_info["bbox"][2]
                    iy1 = img_info["bbox"][3]
                    overlap = (
                        max(0, min(x1, ix1) - max(x0, ix0)) *
                        max(0, min(y1, iy1) - max(y0, iy0))
                    )
                    if overlap > best_overlap:
                        best_overlap = overlap
                        best_xref = img_info.get("xref")
                if best_xref:
                    return fitz_doc.extract_image(best_xref)["image"]

            # Fallback: return first image on page
            return fitz_doc.extract_image(img_list[0][0])["image"] if img_list else None

        except Exception as exc:
            logger.debug("_extract_image_bytes_by_bbox: %s", exc)
            return None

    def _find_figure_node(struct_tree, target_mcids: list):
        """Recursively walk StructTreeRoot to find Figure node matching MCIDs."""
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
            # Find a figure with no alt text
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

            # Extract image bytes by bbox overlap
            img_bytes = _extract_image_bytes_by_bbox(page_index, bbox)
            if not img_bytes:
                results.append(_skipped("4.1.2", issue_key,
                                        f"Could not extract image bytes for figure '{fig_id}'"))
                continue

            # Get nearby text for context
            nearby_text = _get_nearby_text(page_index, bbox)

            # Call GPT-4o vision
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

            # Validate response
            if not alt_text or "cannot" in alt_text.lower() or len(alt_text) > 200:
                results.append(_skipped("4.1.2", issue_key,
                                        f"GPT-4o returned unusable alt text: '{alt_text}'"))
                continue

            # Find Figure node in StructTreeRoot and write /Alt
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


# ═══════════════════════════════════════════════════════════════
# FIXER REGISTRY — DO NOT MODIFY ORDER
# Hala's document-level fixes run first, then Jana, then Sandra
# ═══════════════════════════════════════════════════════════════

FIXERS = [
    # Batch 2 — Hala
    fix_3_1_1_language,
    fix_2_4_2_title,
    fix_2_4_1_and_2_4_5_bookmarks,
    fix_2_4_4_link_purpose,
    # Batch 1 — Jana
    fix_1_1_1_image_alt_text,
    fix_1_1_1_control_name,
    fix_1_4_3_contrast,
    fix_2_5_3_label_in_name,
    fix_1_4_1_color_only,
    fix_1_4_11_non_text_contrast,
    # Batch 3 — Sandra
    fix_3_3_2_form_tooltips,
    fix_4_1_2_figure_alt,
    fix_4_1_2_checkbox_state,
    fix_2_1_1_tab_order,
]


# ═══════════════════════════════════════════════════════════════
# MAIN ENTRY POINT — DO NOT MODIFY
# ═══════════════════════════════════════════════════════════════

def apply_corrections(
    original_pdf_path: str,
    issues: list[dict],
    doc_json: dict,
    output_path: str,
) -> dict:
    """
    Apply all registered fixes to a copy of the original PDF.

    Parameters
    ----------
    original_pdf_path : str   Path to uploaded PDF. Never modified.
    issues            : list  Output of run_wcag_detector().
    doc_json          : dict  Output of extract_document_json().
    output_path       : str   Where to write the corrected PDF.

    Returns
    -------
    dict with: status, corrections, fixed_count, skipped_count, flagged_count
    """
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