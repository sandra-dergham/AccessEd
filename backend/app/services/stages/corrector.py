import logging
import shutil

import pikepdf

from ..wcag.batch_1.correction import (
    fix_1_1_1_image_alt_text,
    fix_1_1_1_control_name,
    fix_1_4_1_color_only,
    fix_1_4_3_contrast,
    fix_1_4_11_non_text_contrast,
    fix_2_5_3_label_in_name,
)
from ..wcag.batch_2.correction import (
    fix_2_4_1_and_2_4_5_bookmarks,
    fix_2_4_2_title,
    fix_2_4_4_link_purpose,
    fix_3_1_1_language,
)
from ..wcag.batch_3.correction import (
    fix_tag_structure,
    fix_2_1_1_tab_order,
    fix_3_3_2_form_tooltips,
    fix_4_1_2_checkbox_state,
    fix_4_1_2_figure_alt,
)

logger = logging.getLogger(__name__)

FIXERS = [
    # Tag structure — must run first so other fixers can attach to the tree
    fix_tag_structure,
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

