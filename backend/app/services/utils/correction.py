from __future__ import annotations

import logging
import re

from typing import Any
import pikepdf

from ..openai_client import get_openai_client

logger = logging.getLogger(__name__)

def _fixed(criterion: str, issue_id: str, detail: str) -> dict:
    """Return this when the fix was applied successfully."""
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



def _set_tooltip_everywhere(field_obj, tooltip: str):
    field_obj["/TU"] = pikepdf.String(tooltip)

    kids = field_obj.get("/Kids")
    if isinstance(kids, pikepdf.Array):
        for kid in kids:
            try:
                kid_obj = kid.get_object()
            except Exception:
                kid_obj = kid

            if isinstance(kid_obj, pikepdf.Dictionary):
                kid_obj["/TU"] = pikepdf.String(tooltip)



