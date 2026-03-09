"""
AccessEd – WCAG Batch 3 Violation Detector
===========================================
File:  backend/app/services/wcag/batch3_rules.py

Batch 3 Guidelines
------------------
  2.1.1  Keyboard
  2.1.2  No Keyboard Trap
  2.1.4  Character Key Shortcuts
  2.2.1  Timing Adjustable
  2.2.2  Pause, Stop, Hide
  2.3.1  Three Flashes or Below Threshold
  3.3.1  Error Identification
  3.3.2  Labels or Instructions
  3.3.3  Error Suggestion
  3.3.4  Error Prevention (Legal, Financial, Data)
  4.1.1  Parsing
  4.1.2  Name, Role, Value
"""

from __future__ import annotations
from typing import Any, Dict, List
from .issue import make_issue


def _page(page_index: int) -> int:
    return page_index + 1


def _collect_nodes(tree):
    flat = []
    def walk(node):
        if not isinstance(node, dict):
            return
        flat.append(node)
        for child in node.get("children", []):
            walk(child)
    for root_node in (tree or []):
        walk(root_node)
    return flat


def _bbox_overlap(a, b):
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    return ax0 < bx1 and ax1 > bx0 and ay0 < by1 and ay1 > by0


_NOT_APPLICABLE = [






    {
        "criterion": "4.1.1",
        "title": "Parsing",
        "rationale": (
            "WCAG 4.1.1 targets HTML/XML markup languages. PDF is a binary "
            "format with its own internal consistency rules validated by tools "
            "such as veraPDF, not by WCAG parsing checks."
        ),
    },
]


def check_not_applicable():
    issues = []
    for entry in _NOT_APPLICABLE:
        issues.append(
            make_issue(
                criterion=entry["criterion"],
                issue=(
                    f"{entry['title']} - Not applicable to static PDF documents. "
                    f"{entry['rationale']}"
                ),
                location={"scope": "document", "page": None},
                severity="not_applicable",
                recommendation=(
                    "No action required for this PDF. If this document is embedded "
                    f"in an interactive web application, evaluate criterion "
                    f"{entry['criterion']} against that application instead."
                ),
            )
        )
    return issues


def check_2_1_1_keyboard(doc_json):
    """
    A) Interactive elements exist but PDF is untagged - AT cannot discover them.
    B) AcroForm fields present but no page defines /Tabs - tab order undefined.
    """
    issues = []
    document      = doc_json.get("document", {})
    structure     = document.get("structure", {})
    interactivity = document.get("interactivity", {})

    has_tags        = structure.get("has_tags", False)
    links           = document.get("links", [])
    has_acroform    = interactivity.get("has_acroform", False)
    acroform_fields = interactivity.get("acroform_fields", [])
    has_tab_order   = interactivity.get("has_tab_order", False)
    tab_order       = interactivity.get("tab_order", [])

    # A: untagged PDF with interactive elements
    if not has_tags and (links or has_acroform):
        parts = []
        if links:
            parts.append(f"{len(links)} link(s)")
        if has_acroform:
            parts.append(f"{len(acroform_fields)} form field(s)")
        issues.append(
            make_issue(
                criterion="2.1.1",
                issue=(
                    f"The PDF contains interactive elements ({', '.join(parts)}) "
                    "but has no tag structure. Assistive technologies rely on the "
                    "tag tree to expose interactive elements to keyboard navigation. "
                    "Without tags, keyboard users using screen readers cannot "
                    "discover or reach these elements."
                ),
                location={"scope": "document", "page": None},
                severity="high",
                recommendation=(
                    "Re-export or remediate the PDF with a tagged structure. "
                    "Ensure all links and form fields are included in the tag tree "
                    "with correct roles (Link, Widget) so assistive technologies "
                    "can present them in a logical tab order."
                ),
            )
        )

    # B: form fields with no tab order defined
    if has_acroform and acroform_fields and not has_tab_order:
        pages_without_tabs = [
            str(_page(e["page_index"]))
            for e in tab_order
            if e.get("tabs") is None
        ]
        issues.append(
            make_issue(
                criterion="2.1.1",
                issue=(
                    f"The PDF contains {len(acroform_fields)} form field(s) but "
                    "no page defines a /Tabs entry to specify tab order "
                    f"(affected pages: {', '.join(pages_without_tabs) or 'all'}). "
                    "Without an explicit tab order, the sequence in which a keyboard "
                    "user moves between fields is undefined and may be illogical."
                ),
                location={"scope": "document", "page": None},
                severity="medium",
                recommendation=(
                    "Set /Tabs to 'S' (structure order) on every page that contains "
                    "form fields. This instructs PDF viewers to follow the logical "
                    "reading order defined by the tag structure when the user presses Tab."
                ),
            )
        )

    if not issues:
        issues.append(
            make_issue(
                criterion="2.1.1",
                issue="No keyboard accessibility violations detected.",
                location={"scope": "document", "page": None},
                severity="pass",
                recommendation="No action required.",
            )
        )

    return issues


def check_2_2_1_timing_adjustable(doc_json):
    """
    Time limits in a PDF can only be implemented via JavaScript.
    No JavaScript = no mechanism for a time limit = not_applicable.
    JavaScript present = risk flag requiring manual review.

    A time limit is only a violation if it exists AND none of the following
    are true:
      Compliance options (at least one must be met):
        (1) The user can turn the time limit off before encountering it
        (2) The user can adjust the time limit to at least 10x the default
        (3) The user is warned before expiry and given >= 20 seconds to
            extend, and can extend at least 10 times
      Exceptions (if any apply, there is no violation):
        (4) The time limit is part of a real-time event where no alternative
            is possible (e.g. a live auction)
        (5) The time limit is essential and extending it would invalidate
            the activity (e.g. a timed exam)
        (6) The time limit is longer than 20 hours

    None of this can be determined statically — manual review is required.
    """
    issues = []
    interactivity = doc_json.get("document", {}).get("interactivity", {})
    has_js   = interactivity.get("has_javascript", False)
    triggers = interactivity.get("javascript_triggers", [])

    if not has_js:
        issues.append(
            make_issue(
                criterion="2.2.1",
                issue=(
                    "No JavaScript was detected. Time limits cannot be implemented "
                    "in a static PDF without scripting. "
                    "This criterion is not applicable."
                ),
                location={"scope": "document", "page": None},
                severity="not_applicable",
                recommendation="No action required.",
            )
        )
    else:
        trigger_summary = "; ".join(
            f"{t['trigger']} at {t['location']}" for t in triggers
        )
        issues.append(
            make_issue(
                criterion="2.2.1",
                issue=(
                    "This PDF contains JavaScript which may implement a time limit. "
                    "A time limit is only a violation if it exists AND none of the "
                    "following are true — Compliance options: (1) the user can turn "
                    "the time limit off before encountering it; (2) the user can "
                    "adjust it to at least 10 times the default duration; (3) the "
                    "user is warned before expiry and given at least 20 seconds to "
                    "extend, with at least 10 extensions allowed. Exceptions: "
                    "(4) the limit is part of a real-time event with no alternative; "
                    "(5) the limit is essential and extending it would invalidate the "
                    "activity; (6) the time limit is longer than 20 hours. "
                    "This cannot be determined statically and requires manual review. "
                    f"JavaScript triggers found: {trigger_summary}."
                ),
                location={"scope": "document", "page": None},
                severity="needs_review",
                recommendation=(
                    "Manually review all JavaScript in this PDF. If a time limit is "
                    "implemented, ensure at least one compliance option is satisfied: "
                    "(1) provide a mechanism to turn the limit off before it starts; "
                    "(2) allow the user to adjust it to at least 10x the default; or "
                    "(3) warn the user before expiry and allow them to extend by at "
                    "least 20 seconds, up to 10 times. If an exception applies "
                    "(real-time event, essential activity, or limit > 20 hours), "
                    "document the rationale."
                ),
            )
        )

    return issues


def check_2_3_1_three_flashes(doc_json):
    """
    Flashing content in a PDF can only be produced via JavaScript.
    Embedded multimedia is the other theoretical source but is explicitly
    out of scope for AccessEd.

    No JavaScript = not_applicable.
    JavaScript present = medium risk flag requiring manual review with
    specialist tooling — confirming a violation requires:
      - Rendering the content at runtime
      - Measuring luminance changes over time
      - Applying the Michelson contrast formula against WCAG thresholds

    A violation exists only if content flashes more than 3 times per second
    AND exceeds either:
      - The general flash threshold (pair of opposing transitions >= 10%
        relative luminance change involving a dark image below 0.80 relative
        luminance), OR
      - The red flash threshold (pair of opposing transitions involving a
        saturated red)
    Content that flashes <= 3 times per second OR stays below both thresholds
    is not a violation.
    """
    issues = []
    interactivity = doc_json.get("document", {}).get("interactivity", {})
    has_js   = interactivity.get("has_javascript", False)
    triggers = interactivity.get("javascript_triggers", [])

    if not has_js:
        issues.append(
            make_issue(
                criterion="2.3.1",
                issue=(
                    "No JavaScript was detected. Flashing content cannot be "
                    "produced in this PDF without scripting. Embedded multimedia "
                    "is out of scope for AccessEd. "
                    "This criterion is not applicable."
                ),
                location={"scope": "document", "page": None},
                severity="not_applicable",
                recommendation="No action required.",
            )
        )
    else:
        trigger_summary = "; ".join(
            f"{t['trigger']} at {t['location']}" for t in triggers
        )
        issues.append(
            make_issue(
                criterion="2.3.1",
                issue=(
                    "This PDF contains JavaScript which may produce rapidly changing "
                    "visual content. Note: embedded multimedia is out of scope for "
                    "AccessEd. "
                    "A violation exists only if content flashes more than 3 times "
                    "per second AND exceeds the general flash threshold (opposing "
                    "luminance transitions >= 10% relative luminance involving a dark "
                    "image below 0.80 relative luminance) or the red flash threshold "
                    "(opposing transitions involving saturated red). Content that "
                    "flashes 3 or fewer times per second, or stays below both "
                    "thresholds, is not a violation. "
                    "Confirming a violation requires rendering the content and "
                    "measuring luminance changes over time using specialist tooling — "
                    "this cannot be determined statically. "
                    f"JavaScript triggers found: {trigger_summary}."
                ),
                location={"scope": "document", "page": None},
                severity="needs_review",
                recommendation=(
                    "Manually review all JavaScript in this PDF for any content that "
                    "changes visual state rapidly. If such content exists, use the "
                    "Photosensitive Epilepsy Analysis Tool (PEAT) or the Harding Flash "
                    "and Pattern Analyser to measure whether the general flash or red "
                    "flash thresholds are exceeded. If thresholds are exceeded, reduce "
                    "the flash rate to 3 or fewer per second or redesign the content "
                    "to stay below the luminance thresholds."
                ),
            )
        )

    return issues


def check_2_2_2_pause_stop_hide(doc_json):
    """
    Moving, blinking, scrolling, or auto-updating content in a PDF can only
    be produced via JavaScript. Embedded multimedia (video, Flash) is another
    theoretical source but is explicitly out of scope for AccessEd per the
    project's Important Notice.

    No JavaScript = no mechanism for such content = not_applicable.
    JavaScript present = risk flag requiring manual review.

    This criterion covers two distinct sub-cases, each with its own conditions:

    Sub-case A – Moving, blinking, or scrolling content:
      A violation exists only if ALL of the following are true:
        (1) the content starts automatically
        (2) it lasts more than 5 seconds
        (3) it is presented in parallel with other content
        (4) no mechanism exists to pause, stop, or hide it
      Exception: if the movement is essential to the activity, no violation.

    Sub-case B – Auto-updating content:
      A violation exists only if ALL of the following are true:
        (1) the content starts automatically
        (2) it is presented in parallel with other content
        (3) no mechanism exists to pause, stop, hide, or control its frequency
      Exception: if auto-updating is essential to the activity, no violation.

    Neither sub-case can be confirmed statically — manual review is required.
    """
    issues = []
    interactivity = doc_json.get("document", {}).get("interactivity", {})
    has_js   = interactivity.get("has_javascript", False)
    triggers = interactivity.get("javascript_triggers", [])

    if not has_js:
        issues.append(
            make_issue(
                criterion="2.2.2",
                issue=(
                    "No JavaScript was detected. Moving, blinking, scrolling, or "
                    "auto-updating content cannot be produced in this PDF without "
                    "scripting. Embedded multimedia is out of scope for AccessEd. "
                    "This criterion is not applicable."
                ),
                location={"scope": "document", "page": None},
                severity="not_applicable",
                recommendation="No action required.",
            )
        )
    else:
        trigger_summary = "; ".join(
            f"{t['trigger']} at {t['location']}" for t in triggers
        )
        issues.append(
            make_issue(
                criterion="2.2.2",
                issue=(
                    "This PDF contains JavaScript which may produce moving, blinking, "
                    "scrolling, or auto-updating content. Note: embedded multimedia is "
                    "out of scope for AccessEd. "
                    "This criterion covers two sub-cases — "
                    "Sub-case A (moving/blinking/scrolling): a violation exists only "
                    "if the content (1) starts automatically, (2) lasts more than 5 "
                    "seconds, and (3) runs in parallel with other content, AND no "
                    "mechanism exists to pause, stop, or hide it (unless the movement "
                    "is essential to the activity). "
                    "Sub-case B (auto-updating): a violation exists only if the content "
                    "(1) starts automatically and (2) runs in parallel with other "
                    "content, AND no mechanism exists to pause, stop, hide, or control "
                    "its update frequency (unless auto-updating is essential). "
                    "Neither sub-case can be confirmed statically and requires manual "
                    f"review. JavaScript triggers found: {trigger_summary}."
                ),
                location={"scope": "document", "page": None},
                severity="needs_review",
                recommendation=(
                    "Manually review all JavaScript in this PDF. "
                    "For any moving, blinking, or scrolling content that starts "
                    "automatically and lasts more than 5 seconds alongside other "
                    "content, provide a mechanism to pause, stop, or hide it. "
                    "For any auto-updating content that starts automatically alongside "
                    "other content, provide a mechanism to pause, stop, hide it, or "
                    "control its update frequency. "
                    "If the behaviour is essential to the activity, document the "
                    "rationale for the exception."
                ),
            )
        )

    return issues


def check_2_1_2_no_keyboard_trap(doc_json):
    """
    A keyboard trap is only a confirmed violation if BOTH conditions are true:
      (1) keyboard focus can be moved INTO a component via keyboard, AND
      (2) keyboard focus CANNOT be moved OUT using Tab, Shift+Tab, Escape,
          or arrow keys (and no alternative exit method is communicated).
    This is a purely behavioral condition — it cannot be confirmed statically.
    JavaScript is the only mechanism in a PDF that could intercept key events,
    so its presence is a risk signal requiring manual testing, not a violation.
    No JavaScript = no scripting mechanism for a trap = pass.
    """
    issues = []
    interactivity = doc_json.get("document", {}).get("interactivity", {})
    has_js   = interactivity.get("has_javascript", False)
    triggers = interactivity.get("javascript_triggers", [])

    if has_js:
        trigger_summary = "; ".join(
            f"{t['trigger']} at {t['location']}" for t in triggers
        )
        issues.append(
            make_issue(
                criterion="2.1.2",
                issue=(
                    "This PDF contains JavaScript which could theoretically "
                    "intercept keyboard events. A keyboard trap is only a confirmed "
                    "violation if a user can move focus INTO a component via keyboard "
                    "but cannot move focus OUT using Tab, Shift+Tab, Escape, or arrow "
                    "keys, and no alternative exit method is communicated. "
                    "This cannot be determined statically and requires manual testing. "
                    f"JavaScript triggers found: {trigger_summary}."
                ),
                location={"scope": "document", "page": None},
                severity="needs_review",
                recommendation=(
                    "Manually test this PDF in Adobe Acrobat Reader using only a "
                    "keyboard: tab into every interactive component and confirm focus "
                    "can be moved away using Tab, Shift+Tab, or Escape. "
                    "Review all JavaScript to ensure no script suppresses or "
                    "overrides standard key events (keydown/keyup for Tab, Escape, "
                    "or arrow keys). If an alternative exit method is required, "
                    "it must be communicated to the user beforehand."
                ),
            )
        )
    else:
        issues.append(
            make_issue(
                criterion="2.1.2",
                issue=(
                    "No JavaScript was detected. There is no scripting mechanism "
                    "in this document that could intercept keyboard events or "
                    "create a keyboard trap."
                ),
                location={"scope": "document", "page": None},
                severity="pass",
                recommendation="No action required.",
            )
        )

    return issues


def check_2_1_4_character_shortcuts(doc_json):
    """
    Single-character shortcuts can only be defined via JavaScript in a PDF.
    No JavaScript = not applicable.
    JavaScript present = needs manual review.

    A single-character shortcut is only a VIOLATION if ALL of the following
    are true:
      (1) it uses only a letter, number, punctuation, or symbol key (no modifier)
      (2) the user CANNOT turn it off
      (3) the user CANNOT remap it to include a non-printable modifier key
          (e.g. Ctrl, Alt)
      (4) it is NOT restricted to activate only when the component has focus

    If at least one escape condition is satisfied, there is no violation.
    This cannot be determined statically — manual review is required.
    """
    issues = []
    interactivity = doc_json.get("document", {}).get("interactivity", {})
    has_js   = interactivity.get("has_javascript", False)
    triggers = interactivity.get("javascript_triggers", [])

    if not has_js:
        issues.append(
            make_issue(
                criterion="2.1.4",
                issue=(
                    "No JavaScript was detected. Single-character keyboard shortcuts "
                    "cannot be defined in this PDF without scripting. "
                    "This criterion is not applicable."
                ),
                location={"scope": "document", "page": None},
                severity="not_applicable",
                recommendation="No action required.",
            )
        )
    else:
        trigger_summary = "; ".join(
            f"{t['trigger']} at {t['location']}" for t in triggers
        )
        issues.append(
            make_issue(
                criterion="2.1.4",
                issue=(
                    "This PDF contains JavaScript which may implement single-character "
                    "keyboard shortcuts. A shortcut is only a violation if it uses "
                    "solely a letter, number, punctuation, or symbol key AND none of "
                    "the following escape conditions are met: (1) the user can turn "
                    "the shortcut off; (2) the user can remap it to include a "
                    "non-printable modifier key (e.g. Ctrl, Alt); (3) the shortcut "
                    "is only active when the relevant component has focus. "
                    "This cannot be determined statically and requires manual review. "
                    f"JavaScript triggers found: {trigger_summary}."
                ),
                location={"scope": "document", "page": None},
                severity="needs_review",
                recommendation=(
                    "Manually review all JavaScript in this PDF. For any "
                    "single-character shortcut found, ensure at least one of the "
                    "following is true: (1) a mechanism exists to turn the shortcut "
                    "off; (2) the shortcut can be remapped to include a modifier key "
                    "(e.g. Ctrl+K instead of just K); (3) the shortcut is only active "
                    "when the specific component has focus."
                ),
            )
        )

    return issues


def check_3_3_1_error_identification(doc_json):
    """
    3.3.1 is conditional: it only applies IF automatic error detection exists.
    "If an input error is automatically detected..." — if it isn't, the
    criterion never triggers and is not_applicable.

    In a PDF, automatic error detection means AcroForm fields with validation
    logic (per-field /AA /V actions or document-level JavaScript).

      A) No AcroForm fields, or all read-only → not_applicable
      B) Interactive fields exist but NO validation anywhere → not_applicable
         (criterion condition never triggered)
      C) Validation exists on some/all fields → needs_review: cannot confirm
         statically that errors identify the field and describe it in text
      Mixed: unvalidated fields alongside validated ones → not_applicable
         for the unvalidated ones (criterion doesn't trigger for them)
    """
    issues = []
    document      = doc_json.get("document", {})
    interactivity = document.get("interactivity", {})

    has_acroform    = interactivity.get("has_acroform", False)
    acroform_fields = interactivity.get("acroform_fields", [])
    has_js          = interactivity.get("has_javascript", False)

    # A: no form fields
    if not has_acroform or not acroform_fields:
        issues.append(
            make_issue(
                criterion="3.3.1",
                issue=(
                    "No AcroForm fields were found in this PDF. "
                    "There is no user input to validate. "
                    "This criterion is not applicable."
                ),
                location={"scope": "document", "page": None},
                severity="not_applicable",
                recommendation="No action required.",
            )
        )
        return issues

    # Only interactive (non-read-only) fields can have input errors
    interactive_fields = [f for f in acroform_fields if not f.get("read_only")]

    if not interactive_fields:
        issues.append(
            make_issue(
                criterion="3.3.1",
                issue=(
                    "All AcroForm fields in this PDF are read-only. "
                    "There is no user input to validate. "
                    "This criterion is not applicable."
                ),
                location={"scope": "document", "page": None},
                severity="not_applicable",
                recommendation="No action required.",
            )
        )
        return issues

    # Split fields into those with and without validation actions
    fields_with_validation = [
        f for f in interactive_fields
        if f.get("validation_actions")
    ]
    fields_without_validation = [
        f for f in interactive_fields
        if not f.get("validation_actions")
    ]

    # B: no validation anywhere — criterion never triggers, not_applicable
    if not fields_with_validation and not has_js:
        field_list = ", ".join(
            f"'{f.get('name') or f['id']}'" for f in interactive_fields
        )
        issues.append(
            make_issue(
                criterion="3.3.1",
                issue=(
                    f"This PDF contains {len(interactive_fields)} interactive "
                    f"form field(s) ({field_list}) but no validation actions "
                    "were detected on any field (/AA with /V) and no document-level "
                    "JavaScript is present. No automatic error detection exists, "
                    "so the criterion condition ('if an input error is automatically "
                    "detected') is never triggered. This criterion is not applicable."
                ),
                location={"scope": "document", "page": None},
                severity="not_applicable",
                recommendation=(
                    "No action required under 3.3.1. However, if any field has input "
                    "constraints (e.g. required format, allowed values), consider "
                    "adding validation so users are informed of errors — this would "
                    "also bring 3.3.1 into scope."
                ),
            )
        )
        return issues

    # C: some fields have validation — cannot confirm it meets 3.3.1 statically
    if fields_with_validation:
        validated_list = ", ".join(
            f"'{f.get('name') or f['id']}'" for f in fields_with_validation
        )
        issues.append(
            make_issue(
                criterion="3.3.1",
                issue=(
                    f"Validation actions were detected on "
                    f"{len(fields_with_validation)} field(s) "
                    f"({validated_list}). It cannot be confirmed statically that "
                    "when an error is detected: (1) the specific field in error is "
                    "identified to the user, and (2) the error is described in text. "
                    "Manual review of the validation logic is required."
                ),
                location={"scope": "document", "page": None},
                severity="needs_review",
                recommendation=(
                    "Manually review the validation JavaScript on each flagged field. "
                    "Confirm that error handling: (1) clearly identifies which field "
                    "is in error (e.g. by referencing the field label in the message), "
                    "and (2) describes the error in plain text "
                    "(e.g. 'Email address must contain @' rather than just 'Invalid input')."
                ),
            )
        )

    # Mixed coverage: unvalidated fields alongside validated ones —
    # criterion still doesn't trigger for unvalidated fields, so not_applicable
    if fields_without_validation and fields_with_validation:
        unvalidated_list = ", ".join(
            f"'{f.get('name') or f['id']}'" for f in fields_without_validation
        )
        issues.append(
            make_issue(
                criterion="3.3.1",
                issue=(
                    f"{len(fields_without_validation)} interactive field(s) "
                    f"({unvalidated_list}) have no validation actions. Since no "
                    "automatic error detection exists for these fields, criterion "
                    "3.3.1 does not apply to them. However, if they have input "
                    "constraints, users will receive no feedback when they enter "
                    "incorrect data."
                ),
                location={"scope": "document", "page": None},
                severity="not_applicable",
                recommendation=(
                    "Consider adding validation to these fields if they have input "
                    "constraints. Doing so would bring them into 3.3.1 scope and "
                    "require that errors are identified and described in text."
                ),
            )
        )

    return issues


def _is_descriptive_field_name(name: str) -> bool:
    """
    Heuristic to determine if a /T field name is likely human-readable
    and descriptive enough to serve as a fallback accessible label.

    Returns False (not descriptive) if the name:
      - Is purely numeric
      - Looks like an auto-generated path (contains brackets e.g. field[0])
      - Is shorter than 3 characters
      - Contains no vowels (likely an abbreviation e.g. "fld", "txt")

    Returns True (possibly descriptive) if the name:
      - Reads like a natural word or phrase (e.g. "EmailAddress", "First Name")
      - Contains spaces
      - Is 3+ characters with at least one vowel
    """
    if not name:
        return False

    # Strip whitespace
    stripped = name.strip()

    # Purely numeric
    if stripped.isdigit():
        return False

    # Auto-generated path pattern e.g. "topmostSubform[0].Page1[0].field3[1]"
    if "[" in stripped or "]" in stripped:
        return False

    # Too short
    if len(stripped) < 3:
        return False

    # No vowels (case-insensitive)
    if not any(c in "aeiouAEIOU" for c in stripped):
        return False

    return True


def check_3_3_4_error_prevention(doc_json):
    """
    3.3.4 only applies to submissions that cause legal commitments, financial
    transactions, modify/delete user data in storage, or submit test responses.
    We cannot determine the submission context statically — only whether a
    submit action exists.

    No submit action = not_applicable (PDF makes no submission).
    Submit action exists = needs_review: reviewer must determine:
      (1) whether the submission context triggers 3.3.4, AND
      (2) whether at least one compliance mechanism is present:
            - Reversible: submission can be undone (server-side)
            - Checked: input is validated and user can correct before final submit
            - Confirmed: a review/confirm step exists before finalising

    We provide a partial signal for "Checked": if validation actions exist on
    fields, that partially satisfies the Checked mechanism — but a correction
    opportunity must also be present, which we cannot confirm statically.
    """
    issues = []
    interactivity   = doc_json.get("document", {}).get("interactivity", {})
    has_acroform    = interactivity.get("has_acroform", False)
    has_submit      = interactivity.get("has_submit_action", False)
    submit_actions  = interactivity.get("submit_actions", [])
    acroform_fields = interactivity.get("acroform_fields", [])

    # not_applicable: no form or no submit action
    if not has_acroform or not has_submit:
        issues.append(
            make_issue(
                criterion="3.3.4",
                issue=(
                    "No form submission action was detected in this PDF. "
                    "The document does not submit data, so no legal, financial, "
                    "or data-modifying transaction can occur. "
                    "This criterion is not applicable."
                ),
                location={"scope": "document", "page": None},
                severity="not_applicable",
                recommendation="No action required.",
            )
        )
        return issues

    # Submit action exists — check for partial "Checked" signal
    interactive_fields      = [f for f in acroform_fields if not f.get("read_only")]
    fields_with_validation  = [f for f in interactive_fields if f.get("validation_actions")]
    has_partial_checked     = len(fields_with_validation) > 0

    submit_urls = ", ".join(
        a.get("url") or "unknown URL"
        for a in submit_actions
    )

    partial_checked_note = (
        f"{len(fields_with_validation)} field(s) have validation actions, which "
        "partially satisfies the 'Checked' mechanism — but a correction opportunity "
        "before final submission must also be confirmed manually. "
        if has_partial_checked else
        "No validation actions were detected on any field, so the 'Checked' "
        "mechanism is not partially satisfied. "
    )

    issues.append(
        make_issue(
            criterion="3.3.4",
            issue=(
                f"This PDF contains a form submission action (target: {submit_urls}). "
                "Criterion 3.3.4 applies if the submission causes a legal commitment, "
                "financial transaction, modifies/deletes user-controllable data, or "
                "submits test responses. This cannot be determined statically. "
                f"{partial_checked_note}"
                "Manual review is required to determine: (1) whether the submission "
                "context triggers 3.3.4, and (2) whether at least one compliance "
                "mechanism is present — Reversible (submission can be undone), "
                "Checked (input validated with correction opportunity before final "
                "submit), or Confirmed (review/confirm step before finalising)."
            ),
            location={"scope": "document", "page": None},
            severity="needs_review",
            recommendation=(
                "First determine whether the submission context is legal, financial, "
                "data-modifying, or test-related. If it is, ensure at least one of: "
                "(1) Reversible — provide a mechanism to undo or cancel the submission "
                "after it is sent (server-side); "
                "(2) Checked — validate all input and give the user an explicit "
                "opportunity to review and correct errors before the final submission; "
                "(3) Confirmed — present a summary/review screen where the user can "
                "verify and correct all data before finalising."
            ),
        )
    )

    return issues


def check_3_3_3_error_suggestion(doc_json):
    """
    3.3.3 is doubly conditional — it only applies if BOTH are true:
      (1) an input error is automatically detected (validation exists), AND
      (2) correction suggestions are known

    If either condition is absent, there is no violation.
    We cannot determine condition (2) statically — we can only detect (1).

    Additionally, the criterion has a security/purpose exception:
    suggestions must NOT be provided if they would jeopardize security
    (e.g. password fields, signature fields). We detect likely sensitive
    fields by type (Sig) or name pattern (password, pin, secret, etc.)

    Situations:
      A) No AcroForm fields, or all read-only → not_applicable
      B) Interactive fields, no validation anywhere → not_applicable
         (condition 1 never triggered)
      C) Validation exists on some fields → needs_review, noting:
         - Fields that appear security-sensitive (exception likely applies)
         - Fields that appear non-sensitive (suggestions expected if known)
    """
    issues = []
    interactivity   = doc_json.get("document", {}).get("interactivity", {})
    has_acroform    = interactivity.get("has_acroform", False)
    acroform_fields = interactivity.get("acroform_fields", [])
    has_js          = interactivity.get("has_javascript", False)

    # A: no form fields
    if not has_acroform or not acroform_fields:
        issues.append(
            make_issue(
                criterion="3.3.3",
                issue=(
                    "No AcroForm fields were found in this PDF. "
                    "There is no user input to validate. "
                    "This criterion is not applicable."
                ),
                location={"scope": "document", "page": None},
                severity="not_applicable",
                recommendation="No action required.",
            )
        )
        return issues

    interactive_fields = [f for f in acroform_fields if not f.get("read_only")]

    if not interactive_fields:
        issues.append(
            make_issue(
                criterion="3.3.3",
                issue=(
                    "All AcroForm fields in this PDF are read-only. "
                    "There is no user input to validate. "
                    "This criterion is not applicable."
                ),
                location={"scope": "document", "page": None},
                severity="not_applicable",
                recommendation="No action required.",
            )
        )
        return issues

    fields_with_validation = [
        f for f in interactive_fields
        if f.get("validation_actions")
    ]

    # B: no validation anywhere — condition (1) never triggered
    if not fields_with_validation and not has_js:
        issues.append(
            make_issue(
                criterion="3.3.3",
                issue=(
                    "No validation actions were detected on any interactive field. "
                    "Since no automatic error detection exists, the first condition "
                    "of criterion 3.3.3 ('if an input error is automatically "
                    "detected') is never triggered. This criterion is not applicable."
                ),
                location={"scope": "document", "page": None},
                severity="not_applicable",
                recommendation=(
                    "No action required under 3.3.3. If validation is added in "
                    "future, ensure that correction suggestions are provided where "
                    "known, unless doing so would jeopardize security or purpose."
                ),
            )
        )
        return issues

    # C: validation exists — classify fields as sensitive or non-sensitive
    _SENSITIVE_PATTERNS = {
        "password", "passwd", "pwd", "pin", "secret", "token",
        "passphrase", "credential", "auth"
    }

    def _is_sensitive(field: Dict[str, Any]) -> bool:
        # Signature fields are always sensitive
        if field.get("type") == "Sig":
            return True
        # Name pattern match (case-insensitive)
        name = (field.get("name") or "").lower()
        tooltip = (field.get("tooltip") or "").lower()
        return any(
            p in name or p in tooltip
            for p in _SENSITIVE_PATTERNS
        )

    sensitive_fields     = [f for f in fields_with_validation if _is_sensitive(f)]
    non_sensitive_fields = [f for f in fields_with_validation if not _is_sensitive(f)]

    if non_sensitive_fields:
        non_sensitive_list = ", ".join(
            f"'{f.get('name') or f['id']}'" for f in non_sensitive_fields
        )
        issues.append(
            make_issue(
                criterion="3.3.3",
                issue=(
                    f"Validation actions were detected on {len(non_sensitive_fields)} "
                    f"non-sensitive field(s) ({non_sensitive_list}). It cannot be "
                    "confirmed statically that when an error is detected, correction "
                    "suggestions are provided where known. Manual review is required."
                ),
                location={"scope": "document", "page": None},
                severity="needs_review",
                recommendation=(
                    "Manually review the validation logic on each flagged field. "
                    "If the error type has a known correction (e.g. 'must be a valid "
                    "email address', 'must be a date in DD/MM/YYYY format'), ensure "
                    "that suggestion is communicated to the user in the error message. "
                    "Generic messages like 'Invalid input' are not sufficient."
                ),
            )
        )

    if sensitive_fields:
        sensitive_list = ", ".join(
            f"'{f.get('name') or f['id']}'" for f in sensitive_fields
        )
        issues.append(
            make_issue(
                criterion="3.3.3",
                issue=(
                    f"{len(sensitive_fields)} field(s) appear security-sensitive "
                    f"({sensitive_list}, e.g. password or signature fields). "
                    "The security/purpose exception likely applies — correction "
                    "suggestions should NOT be provided for these fields as doing "
                    "so could jeopardize security. Manual confirmation is recommended."
                ),
                location={"scope": "document", "page": None},
                severity="needs_review",
                recommendation=(
                    "Confirm that no correction suggestions are exposed for password, "
                    "PIN, signature, or other security-sensitive fields. "
                    "Revealing why a credential is wrong (e.g. 'too short', "
                    "'missing special character') can aid attackers."
                ),
            )
        )

    return issues


def check_3_3_2_labels(doc_json):
    """
    In a PDF, 3.3.2 applies to AcroForm fields.
    Each interactive field should have /TU (tooltip) as its accessible label.
    /T (field name) alone is a weaker fallback - flagged at low severity.
    """
    issues = []
    interactivity   = doc_json.get("document", {}).get("interactivity", {})
    has_acroform    = interactivity.get("has_acroform", False)
    acroform_fields = interactivity.get("acroform_fields", [])

    if not has_acroform or not acroform_fields:
        issues.append(
            make_issue(
                criterion="3.3.2",
                issue=(
                    "No AcroForm fields were found in this PDF. "
                    "Criterion 3.3.2 is not applicable."
                ),
                location={"scope": "document", "page": None},
                severity="not_applicable",
                recommendation="No action required.",
            )
        )
        return issues

    interactive_fields = [f for f in acroform_fields if not f.get("read_only")]

    # High severity: no label at all
    for field in interactive_fields:
        if not field.get("tooltip") and not field.get("name"):
            page_no = _page(field["page_index"]) if field.get("page_index") is not None else None
            issues.append(
                make_issue(
                    criterion="3.3.2",
                    issue=(
                        f"Form field '{field['id']}' "
                        f"(type: {field.get('type', 'unknown')}) has no accessible "
                        "label. Neither a /TU tooltip nor a /T field name is present."
                    ),
                    location={
                        "scope":      "acroform",
                        "page":       page_no,
                        "field_id":   field["id"],
                        "field_type": field.get("type"),
                    },
                    severity="high",
                    recommendation=(
                        "Add a /TU (tooltip) attribute to this form field describing "
                        "its purpose (e.g. 'First Name', 'Email Address'). Screen "
                        "readers announce /TU as the field label when the user tabs in."
                    ),
                )
            )

    # Medium or low severity: /T only, no /TU
    for field in interactive_fields:
        if not field.get("tooltip") and field.get("name"):
            page_no = _page(field["page_index"]) if field.get("page_index") is not None else None
            name = field.get("name")
            descriptive = _is_descriptive_field_name(name)

            if descriptive:
                severity = "low"
                descriptor_note = (
                    f"The field name '{name}' appears possibly human-readable "
                    "but was not designed as an accessible label and may not be "
                    "descriptive enough for all users."
                )
            else:
                severity = "medium"
                descriptor_note = (
                    f"The field name '{name}' appears auto-generated or "
                    "non-descriptive and is unlikely to be meaningful to users "
                    "when announced by a screen reader."
                )

            issues.append(
                make_issue(
                    criterion="3.3.2",
                    issue=(
                        f"Form field '{field['id']}' has a field name (/T: '{name}') "
                        f"but no tooltip (/TU). {descriptor_note} "
                        "Screen readers use /TU as the primary accessible label; "
                        "/T is only a last-resort fallback."
                    ),
                    location={
                        "scope":      "acroform",
                        "page":       page_no,
                        "field_id":   field["id"],
                        "field_name": name,
                    },
                    severity=severity,
                    recommendation=(
                        "Add a /TU tooltip attribute with a clear, human-readable "
                        "label describing the field's purpose "
                        "(e.g. 'Email address', 'Date of birth (DD/MM/YYYY)'). "
                        "Screen readers announce /TU when the user tabs into the field."
                    ),
                )
            )

    if not any(not f.get("tooltip") for f in interactive_fields):
        issues.append(
            make_issue(
                criterion="3.3.2",
                issue="All form fields have accessible labels.",
                location={"scope": "document", "page": None},
                severity="pass",
                recommendation="No action required.",
            )
        )

    return issues


def check_4_1_2_name_role_value(doc_json):
    """
    Three requirements from the criterion:

    Requirement 1 — Name and Role:
      A) Untagged PDF → no element has a programmatically determinable role
      B) Links with no accessible name (no overlapping text, no tagged name)
      C) Figure nodes with no /Alt or /ActualText
      D) Form fields with no Widget role in the tag tree (role missing)
      E) Form fields with no accessible name and no tag covering them

    Requirement 2 — States, properties, and values:
      F) Checkbox/radio fields (type Btn, not push-button) with no /AS
         appearance state → checked/unchecked state not programmatically
         determinable

    Requirement 3 — Notification of changes:
      Not detectable statically → not_applicable for this sub-requirement
    """
    issues = []
    document      = doc_json.get("document", {})
    structure     = document.get("structure", {})
    interactivity = document.get("interactivity", {})
    has_tags      = structure.get("has_tags", False)
    tree          = structure.get("tree") or []
    flat_nodes    = _collect_nodes(tree)

    # ── A: untagged PDF ───────────────────────────────────────────────────────
    if not has_tags:
        issues.append(
            make_issue(
                criterion="4.1.2",
                issue=(
                    "The PDF contains no tag structure (/StructTreeRoot is absent). "
                    "Assistive technologies cannot determine the role, name, or value "
                    "of any element in this document."
                ),
                location={"scope": "document", "page": None},
                severity="high",
                recommendation=(
                    "Re-export or remediate the PDF with a tagged structure. "
                    "Use Adobe Acrobat Pro, axesPDF, or PAC 3 to add proper tags "
                    "so every element has a programmatically determinable role, "
                    "name, and value."
                ),
            )
        )

    # ── B: links without accessible names ────────────────────────────────────
    links      = document.get("links", [])
    text_spans = document.get("text_spans", [])

    spans_by_page: Dict[int, List[Dict[str, Any]]] = {}
    for span in text_spans:
        spans_by_page.setdefault(span["page_index"], []).append(span)

    tagged_link_has_name = any(
        n.get("role") == "Link" and (n.get("alt") or n.get("actual_text"))
        for n in flat_nodes
    )

    for link in links:
        page_idx  = link.get("page_index", 0)
        link_bbox = link.get("bbox")
        link_id   = link.get("id", "unknown")
        if not link_bbox:
            continue

        overlapping_text = "".join(
            span.get("text", "")
            for span in spans_by_page.get(page_idx, [])
            if _bbox_overlap(link_bbox, span.get("bbox", [0, 0, 0, 0]))
        ).strip()

        if not overlapping_text and not tagged_link_has_name:
            issues.append(
                make_issue(
                    criterion="4.1.2",
                    issue=(
                        f"Link '{link_id}' on page {_page(page_idx)} has no "
                        "accessible name. No visible text overlaps the link "
                        "annotation and no /Alt or /ActualText is present in "
                        "the tag tree for any Link element."
                    ),
                    location={
                        "page":    _page(page_idx),
                        "element": link_id,
                        "bbox":    link_bbox,
                        "kind":    link.get("kind"),
                        "target":  link.get("target"),
                    },
                    severity="high",
                    recommendation=(
                        "Ensure every link has a descriptive visible label, or add "
                        "an /Alt or /ActualText entry to the corresponding Link tag "
                        "so screen readers can announce the link's purpose."
                    ),
                )
            )

    # ── C: Figure nodes without accessible names ──────────────────────────────
    for node in flat_nodes:
        if node.get("role") != "Figure":
            continue
        if not node.get("alt") and not node.get("actual_text"):
            issues.append(
                make_issue(
                    criterion="4.1.2",
                    issue=(
                        "A tagged <Figure> element in the structure tree has no "
                        "/Alt or /ActualText entry. Its name cannot be "
                        "programmatically determined by assistive technology."
                    ),
                    location={
                        "scope": "structure_tree",
                        "role":  "Figure",
                        "depth": node.get("depth"),
                    },
                    severity="high",
                    recommendation=(
                        "Add an /Alt attribute to the <Figure> tag describing the "
                        "image content, or supply /ActualText if the figure contains "
                        "embedded text that should be read aloud."
                    ),
                )
            )

    # ── D & E: form fields — role and name ────────────────────────────────────
    acroform_fields = interactivity.get("acroform_fields", [])
    interactive_fields = [f for f in acroform_fields if not f.get("read_only")]

    # Count Widget nodes in the tag tree
    widget_nodes_in_tree = [n for n in flat_nodes if n.get("role") == "Widget"]

    for field in interactive_fields:
        field_id = field.get("id", "unknown")
        page_no  = _page(field["page_index"]) if field.get("page_index") is not None else None

        # D: no Widget role in tag tree for this field
        # Heuristic: if there are fewer Widget nodes than interactive fields,
        # some fields are not represented in the tag tree
        if len(widget_nodes_in_tree) < len(interactive_fields):
            issues.append(
                make_issue(
                    criterion="4.1.2",
                    issue=(
                        f"The PDF has {len(interactive_fields)} interactive form "
                        f"field(s) but only {len(widget_nodes_in_tree)} Widget node(s) "
                        "in the tag tree. Some form fields have no programmatically "
                        "determinable role — assistive technologies cannot identify "
                        "them as form controls."
                    ),
                    location={"scope": "document", "page": None},
                    severity="high",
                    recommendation=(
                        "Ensure every interactive form field has a corresponding "
                        "Widget tag in the structure tree. Re-export or remediate "
                        "the PDF so all form fields are properly tagged."
                    ),
                )
            )
            break  # One document-level issue is sufficient

        # E: field has no accessible name (no /TU, no /T)
        if not field.get("tooltip") and not field.get("name"):
            issues.append(
                make_issue(
                    criterion="4.1.2",
                    issue=(
                        f"Form field '{field_id}' "
                        f"(type: {field.get('type', 'unknown')}) has no accessible "
                        "name. Neither /TU (tooltip) nor /T (field name) is present. "
                        "Assistive technologies cannot announce the field's purpose "
                        "to the user."
                    ),
                    location={
                        "scope":      "acroform",
                        "page":       page_no,
                        "field_id":   field_id,
                        "field_type": field.get("type"),
                    },
                    severity="high",
                    recommendation=(
                        "Add a /TU tooltip attribute describing the field's purpose "
                        "(e.g. 'Email address', 'Date of birth'). This is the name "
                        "announced by screen readers when the user tabs into the field."
                    ),
                )
            )

    # ── F: checkboxes/radio buttons with no appearance state ──────────────────
    # Btn fields: bit 16 of /Ff = push button (exclude), bit 15 = radio button
    # If neither bit set → checkbox
    # We check Btn fields that are not push buttons
    for field in interactive_fields:
        if field.get("type") != "Btn":
            continue

        ff = field.get("flags", 0)
        is_push_button = bool(ff & (1 << 16))  # bit 16 (0-indexed: 15)
        if is_push_button:
            continue  # push buttons don't have checked state

        page_no = _page(field["page_index"]) if field.get("page_index") is not None else None

        if not field.get("appearance_state"):
            field_id = field.get("id", "unknown")
            is_radio = bool(ff & (1 << 14))  # bit 15 (0-indexed: 14)
            kind     = "radio button" if is_radio else "checkbox"
            issues.append(
                make_issue(
                    criterion="4.1.2",
                    issue=(
                        f"A {kind} field '{field_id}' has no /AS (appearance state) "
                        "entry. Assistive technologies cannot programmatically "
                        "determine whether it is checked or unchecked — users will "
                        "not be informed of the current state."
                    ),
                    location={
                        "scope":      "acroform",
                        "page":       page_no,
                        "field_id":   field_id,
                        "field_type": kind,
                    },
                    severity="high",
                    recommendation=(
                        f"Ensure the {kind} field defines an /AS appearance state "
                        "entry (e.g. /Yes for checked, /Off for unchecked). This "
                        "allows assistive technologies to announce the current state "
                        "when the user navigates to or interacts with the field."
                    ),
                )
            )

    # ── Requirement 3: notification of changes ────────────────────────────────
    if interactive_fields:
        issues.append(
            make_issue(
                criterion="4.1.2",
                issue=(
                    "Requirement 3 of 4.1.2 (notification of changes to states, "
                    "properties, and values must be available to assistive "
                    "technologies) cannot be evaluated statically. It requires "
                    "live interaction between the PDF viewer and assistive technology."
                ),
                location={"scope": "document", "page": None},
                severity="not_applicable",
                recommendation=(
                    "Test the document in Adobe Acrobat Reader with a screen reader "
                    "(e.g. NVDA or JAWS) to confirm that state changes on interactive "
                    "elements (checkboxes, dropdowns, text fields) are announced."
                ),
            )
        )

    if not issues:
        issues.append(
            make_issue(
                criterion="4.1.2",
                issue="No Name/Role/Value violations detected.",
                location={"scope": "document", "page": None},
                severity="pass",
                recommendation="No action required.",
            )
        )

    return issues


def run_batch3_rules(doc_json: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Run all Batch 3 checks and return a flat list of issues.
    Called by detector.py.
    """
    issues: List[Dict[str, Any]] = []

    issues.extend(check_not_applicable())              # 4.1.1
    issues.extend(check_2_1_1_keyboard(doc_json))
    issues.extend(check_2_1_2_no_keyboard_trap(doc_json))
    issues.extend(check_2_1_4_character_shortcuts(doc_json))
    issues.extend(check_2_2_1_timing_adjustable(doc_json))
    issues.extend(check_2_2_2_pause_stop_hide(doc_json))
    issues.extend(check_2_3_1_three_flashes(doc_json))
    issues.extend(check_3_3_1_error_identification(doc_json))
    issues.extend(check_3_3_2_labels(doc_json))
    issues.extend(check_3_3_3_error_suggestion(doc_json))
    issues.extend(check_3_3_4_error_prevention(doc_json))
    issues.extend(check_4_1_2_name_role_value(doc_json))

    return issues