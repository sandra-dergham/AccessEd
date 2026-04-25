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
    WCAG 1.1.1 - image_missing_text_alternative (severity: high)
    Owner: Jana | Method: GPT-4o vision

    DATA AVAILABLE:
      issue["location"]["image_id"]  -> ID of the image occurrence
      issue["location"]["page"]      -> page index (0-based)

      doc_json["document"]["images"]["occurrences"]
        each occ has:
          occ["id"]               matches image_id
          occ["asset_id"]         use to extract image bytes
          occ["bbox"]             [x0, y0, x1, y1]
          occ["struct_figure_id"] ID of the Figure node (may be None)

      Image bytes: re-open original_pdf_path with fitz
        import fitz
        doc = fitz.open(original_pdf_path)
        page = doc.load_page(page_index)
        for img in page.get_image_list(full=True):
            xref = img[0]
            base_image = doc.extract_image(xref)
            img_bytes = base_image["image"]

      Surrounding text: filter text_spans on same page near occ["bbox"]

    FIX STEPS:
      1. Loop over filtered issues
      2. Find image occurrence by image_id in doc_json["document"]["images"]["occurrences"]
      3. Extract image bytes using fitz
      4. Collect nearby text_spans as context string
      5. Base64-encode -> call GPT-4o vision API
         Prompt: "Describe this image in one concise sentence (max 125 characters)
                  suitable as alt text for a PDF. Context: {nearby_text}.
                  Return ONLY the alt text. No explanation. No quotes."
      6. Validate response (not empty, not "I cannot", <= 200 chars)
      7. Find Figure node by struct_figure_id -> write node["/Alt"] via pikepdf
         If no struct_figure_id: create a new Figure tag
      8. Return _fixed(...) on success, _skipped(...) on failure
    """
    # Jana implements here
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
    """
    WCAG 1.1.1 - control_missing_name (severity: high)
    Owner: Jana | Method: Pure code

    DATA AVAILABLE:
      issue["location"]["widget_id"]  -> widget ID e.g. "widget_0"
      issue["location"]["page"]       -> page index

      doc_json["document"]["widgets"]
        each widget has:
          widget["id"]         matches location["widget_id"]
          widget["field_name"] the /T field name string <- clean this

      doc_json["document"]["interactivity"]["acroform_fields"]
        each field has: id, name (/T), tooltip (/TU=None=problem), page_index

    FIX STEPS:
      1. Get widget_id from location
      2. Find widget in doc_json["document"]["widgets"] by id
      3. Get widget["field_name"] -> this is the /T string
      4. If None or empty -> _skipped
      5. Clean it: _clean_field_name(field_name)
      6. field_obj = _find_acroform_field(pdf, widget["field_name"])
      7. Write: field_obj["/TU"] = pikepdf.String(clean_name)
      8. Return _fixed(...) on success
    """
    # Jana implements here
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
    """
    WCAG 1.4.3 - insufficient_text_contrast (severity: high)
    Owner: Jana | Method: Pure code (math)

    DATA AVAILABLE:
      issue["location"]["span_id"]        -> ID of the text span
      issue["location"]["contrast_ratio"] -> current ratio e.g. 2.1

      Find span by span_id using _build_span_lookup(doc_json):
        span["color"]["fill_rgb"]               -> [R,G,B] foreground
        span["background_estimate"]["bg_rgb"]   -> [R,G,B] background
        span["contrast"]["large_text_assumed"]  -> True/False
          True  -> target ratio = 3.0
          False -> target ratio = 4.5
        span["bbox"]       -> [x0, y0, x1, y1]
        span["page_index"] -> page number

    FIX STEPS:
      1. Look up span by span_id using _build_span_lookup(doc_json)
      2. Get fg_rgb, bg_rgb, large_text_assumed
      3. Set target = 3.0 if large_text_assumed else 4.5
      4. Compute new fg color: adjust luminance until contrast passes
         (darken if fg is light relative to bg, lighten if fg is dark)
      5. Find span in PDF page content stream by page_index + bbox
      6. Replace color operator (rg/RG) with new RGB values
      7. Return _fixed(...) on success

    NOTE: Step 5-6 (content stream editing) is the hardest part.
    Budget 2 full days. If time is short, use _flagged instead.
    """
    # Jana implements here
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
    """
    WCAG 2.5.3 - label_not_in_name (severity: high)
    Owner: Jana | Method: Pure code

    DATA AVAILABLE:
      issue["location"]["field_id"]          -> field ID
      issue["location"]["widget_id"]         -> widget ID
      issue["location"]["visible_label"]     -> text user sees on screen <- USE THIS
      issue["location"]["programmatic_name"] -> current wrong /TU or /T value

      The visible_label is ALREADY in the issue. No lookup needed.

      To find the field in pikepdf:
        doc_json["document"]["interactivity"]["acroform_fields"]
        -> find by field_id -> get field["name"] (the /T value)
        -> _find_acroform_field(pdf, field["name"])

    FIX STEPS:
      1. Get visible_label from location["visible_label"]
      2. If None or empty -> _skipped
      3. Find field in acroform_fields by field_id -> get field["name"]
      4. field_obj = _find_acroform_field(pdf, field["name"])
      5. Write: field_obj["/TU"] = pikepdf.String(visible_label)
         NOTE: write visible_label EXACTLY as-is. Do NOT clean or modify it.
         The point is /TU must match what the user sees on screen.
      6. Return _fixed(...) on success
    """
    # Jana implements here
    results = []
    targets = _filter_issues(issues, "2.5.3", "label_not_in_name")
    for iss in targets:
        results.append(_skipped("2.5.3", iss.get("issue", ""),
                                "Not yet implemented - Jana"))
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
    Owner: Hala | Method: Pure code — START WITH THIS FIRST

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
    # TODO Hala: write pdf.Root["/Lang"] = pikepdf.String(lang) here
    results.append(_skipped("3.1.1", "language_of_page",
                            "Not yet implemented - Hala"))
    return results


def fix_2_4_2_title(
    pdf: pikepdf.Pdf,
    issues: list[dict],
    doc_json: dict,
    original_pdf_path: str,
) -> list[dict]:
    """
    WCAG 2.4.2 - page_titled (severity: medium)
    Owner: Hala | Method: Pure code

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
    # Hala implements here
    results = []
    targets = _filter_issues(issues, "2.4.2", "page_titled")
    if not targets:
        return results
    # TODO Hala: derive title and write pdf.docinfo["/Title"] here
    results.append(_skipped("2.4.2", "page_titled",
                            "Not yet implemented - Hala"))
    return results


def fix_2_4_1_and_2_4_5_bookmarks(
    pdf: pikepdf.Pdf,
    issues: list[dict],
    doc_json: dict,
    original_pdf_path: str,
) -> list[dict]:
    """
    WCAG 2.4.1 / 2.4.5 - bypass_blocks / multiple_ways
    Owner: Hala | Method: Pure code

    DATA AVAILABLE:
      doc_json["document"]["heading_candidates"]
        -> list of span IDs in document order

      doc_json["document"]["text_spans"]
        each span: id, text, page_index, font["size"]
        -> use font size to infer level: largest font = H1, next = H2

    FIX STEPS:
      1. Check at least one of 2.4.1 or 2.4.5 issues exists
      2. span_lookup = _build_span_lookup(doc_json)
      3. For each heading_candidate ID:
           span = span_lookup[id]
           record (text, page_index, font_size)
      4. Sort unique font sizes desc -> assign levels (largest=1, next=2...)
      5. Build pikepdf /Outlines structure:
           root = pikepdf.Dictionary(Count=pikepdf.Integer(n))
           each item = pikepdf.Dictionary(
               Title=pikepdf.String(text),
               Dest=pikepdf.Array([pdf.pages[page_index].obj, pikepdf.Name("/Fit")]),
               Count=pikepdf.Integer(0),
           )
           chain items with /Next and /Prev
           set /First and /Last on root
      6. pdf.Root["/Outlines"] = pdf.make_indirect(root)
      7. Return _fixed(...) - fixes both 2.4.1 and 2.4.5 at once

    NOTE: Most complex pure-code fix. Budget 2 days.
    """
    # Hala implements here
    results = []
    has_241 = bool(_filter_issues(issues, "2.4.1"))
    has_245 = bool(_filter_issues(issues, "2.4.5"))
    if not has_241 and not has_245:
        return results
    # TODO Hala: build and write /Outlines here
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
    """
    WCAG 2.4.4 - link_purpose (severity: medium)
    Owner: Hala | Method: Pure code (cases A+B) + GPT-4o (case C)

    DATA AVAILABLE:
      issue["location"]["link_id"]  -> link ID e.g. "link_p0_3"
      issue["location"]["page"]     -> page index

      doc_json["document"]["links"]
        each link: id, uri, bbox [x0,y0,x1,y1], type ("uri"/"internal")

      Surrounding context: text_spans on same page near link["bbox"]

    THREE CASES:
      A - raw URL as link text e.g. "https://example.com/report.pdf"
          Fix: strip scheme, clean -> no GPT needed

      B - vague text + readable URL path e.g. uri ends in "/2024-report.pdf"
          Fix: extract last path segment, clean -> no GPT needed

      C - vague text + opaque URL e.g. "https://t.co/xK3p"
          Fix: call GPT-4o
          Prompt: "Link text: '{link_text}'. URL: '{uri}'.
                   Context: '{surrounding_text}'.
                   Write a descriptive label max 60 chars.
                   Return ONLY the label."

    WRITE FIX:
      Find the PDF link annotation on page by matching bbox:
        for annot in pdf.pages[page_index].get("/Annots", []):
            obj = annot.get_object()
            if obj.get("/Subtype") == "/Link":
                if rect matches link["bbox"]:
                    obj["/Contents"] = pikepdf.String(label)
    """
    # Hala implements here
    results = []
    targets = _filter_issues(issues, "2.4.4", "link_purpose")
    for iss in targets:
        results.append(_skipped("2.4.4", iss.get("issue", ""),
                                "Not yet implemented - Hala"))
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
        field_name = loc.get("field_name")  # /T value — present in med/low, None in high
        severity   = iss.get("severity", "")
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
                # CASE HIGH: no /T at all — match by field_id in doc_json
                doc_field = field_by_id.get(field_id)
                if doc_field is None:
                    results.append(_skipped("3.3.2", issue_key,
                                            f"field_id '{field_id}' not found in doc_json"))
                    continue

                field_type = doc_field.get("type") or loc.get("field_type")
                label = TYPE_FALLBACK.get(field_type, "Form field")

                # Walk AcroForm by index to find field with no /T
                acroform = pdf.Root.get("/AcroForm")
                if acroform is None:
                    results.append(_skipped("3.3.2", issue_key, "No AcroForm in PDF"))
                    continue

                # Match by page_index + type since there's no /T to search by
                page_index = doc_field.get("page_index")
                matched = False
                for field_ref in acroform.get("/Fields", []):
                    try:
                        obj = field_ref.get_object()
                        t_val = obj.get("/T")
                        ft_val = str(obj.get("/FT", "")).lstrip("/")
                        if t_val is None and ft_val == field_type:
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
    """
    WCAG 4.1.2 - Figure node missing /Alt in struct tree (severity: high)
    Owner: Sandra | Method: GPT-4o vision

    DATA AVAILABLE:
      doc_json["document"]["structure"]["figures"]
        each fig: id, alt (None=problem), mcids, page_object_ref, children

      doc_json["document"]["images"]["occurrences"]
        each occ: struct_figure_id (matches fig["id"]), asset_id, page_index

      Image bytes: re-open original_pdf_path with fitz (same as Jana's 1.1.1)

    FIX STEPS:
      1. Filter issues: criterion="4.1.2", "Figure" and "/Alt" in issue text
      2. For each: find figure node in doc_json structure figures where alt=None
      3. Find image occurrence via occ["struct_figure_id"] == fig["id"]
      4. Extract image bytes with fitz, call GPT-4o (same prompt as 1.1.1)
      5. Walk pdf.Root["/StructTreeRoot"]["/K"] recursively to find Figure node
         matching by MCIDs or page_object_ref
      6. Write: figure_node["/Alt"] = pikepdf.String(alt_text)
         (Node already exists — just add /Alt to it, simpler than 1.1.1)
      7. Return _fixed(...) on success

    NOTE: The Figure node already exists here. Much simpler than 1.1.1
    because you only need to write /Alt onto an existing node.
    """
    # Sandra implements here
    results = []
    targets = [
        iss for iss in issues
        if iss.get("criterion") == "4.1.2"
        and "Figure" in str(iss.get("issue", ""))
        and "/Alt" in str(iss.get("issue", ""))
        and iss.get("severity") not in {"pass", "not_applicable"}
    ]
    for iss in targets:
        results.append(_skipped("4.1.2", "figure_missing_alt",
                                "Not yet implemented - Sandra"))
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