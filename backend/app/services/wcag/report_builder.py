from .scoring import compute_score
from typing import Any, Dict, List
from fpdf import FPDF
import math
import os
import tempfile


# -----------------------------------------------------------------------------
# AccessEd report builder - design-match accessible
# Goal: preserve the original visual design while improving accessibility:
# - darker, WCAG-safer colors
# - outline-only cards to preserve the layout without failing non-text contrast
# - more generous spacing to reduce resize/overlap detections
# - metadata + bookmarks
# - optional pikepdf post-processing to add a real /StructTreeRoot and page tags
# -----------------------------------------------------------------------------


def build_report(document_meta: dict, issues: list[dict]) -> dict:
    return {
        "meta": document_meta,
        "score": compute_score(issues),
        "issues": issues,
    }


# High-contrast color palette. Same semantic colors as the original report,
# but used as dark accents/text on white cards. Avoid reversed white-on-color
# text because some PDF contrast checkers mis-detect it as 1.0 contrast.
COLORS = {
    "ink": (15, 23, 42),
    "muted": (51, 65, 85),
    "line": (71, 85, 105),      # high-contrast outlines for WCAG 1.4.11
    "strong_line": (100, 116, 139),
    "soft_line": (71, 85, 105),
    "paper": (255, 255, 255),
    "panel": (255, 255, 255),
    "na": (71, 85, 105),
    "review": (29, 78, 216),
    "pass": (4, 120, 87),
    "low": (133, 77, 14),
    "medium": (154, 52, 18),
    "high": (153, 27, 27),
}

SOFT_FILLS = {
    "na": (248, 250, 252),
    "review": (239, 246, 255),
    "pass": (236, 253, 245),
    "low": (254, 252, 232),
    "medium": (255, 237, 213),
    "high": (254, 226, 226),
}

SEVERITY_STYLES = {
    "high": {"color": COLORS["high"], "label": "HIGH"},
    "medium": {"color": COLORS["medium"], "label": "MEDIUM"},
    "low": {"color": COLORS["low"], "label": "LOW"},
    "needs_review": {"color": COLORS["review"], "label": "NEEDS REVIEW"},
}


def _safe(value: Any, default: str = "N/A") -> str:
    """Return text that Helvetica/fpdf2 can render safely.

    Important: do not insert zero-width spaces. Helvetica cannot render U+200B,
    and fpdf2 raises: Character "\u200b" is outside the supported range.
    """
    if value is None:
        return default

    text = str(value).strip()
    if not text:
        return default

    replacements = {
        "\u200b": "",       # zero-width space
        "\u200c": "",       # zero-width non-joiner
        "\u200d": "",       # zero-width joiner
        "\ufeff": "",       # byte-order mark / zero-width no-break space
        "\u2060": "",       # word joiner
        "\u00ad": "-",      # soft hyphen -> visible safe hyphen
        "\u2014": "-",
        "\u2013": "-",
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2026": "...",
        "\xa0": " ",
        "\t": " ",
        "\r": " ",
    }
    for bad, good in replacements.items():
        text = text.replace(bad, good)

    # Helvetica in fpdf2 is effectively Latin-1. Replace unsupported chars
    # instead of crashing report generation.
    text = text.encode("latin-1", "replace").decode("latin-1")

    # Prevent rare layout crashes from very long unbroken tokens without using
    # invisible characters. Most IDs like span_p0_s12 remain unchanged.
    def break_long_token(token: str, limit: int = 60) -> str:
        if len(token) <= limit or " " in token:
            return token
        return " ".join(token[i:i + limit] for i in range(0, len(token), limit))

    text = " ".join(break_long_token(part) for part in text.split(" "))
    return " ".join(text.split())


def _plain(value: Any, default: str = "N/A") -> str:
    """Safe text for metadata/bookmarks."""
    return _safe(value, default)


def _format_location(location: Dict[str, Any]) -> str:
    if not location:
        return "N/A"

    parts = []
    page = location.get("page")
    if page is not None:
        try:
            parts.append(f"Page {int(page) + 1}")
        except Exception:
            parts.append(f"Page {page}")

    for key in ["span_id", "graphic_id", "field_id", "scope"]:
        if location.get(key) is not None:
            parts.append(f"{key}: {location[key]}")

    if location.get("contrast_ratio") is not None:
        parts.append(f"contrast ratio: {location['contrast_ratio']}")

    return " | ".join(parts) if parts else "N/A"


def _group_issues_by_severity(issues: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    grouped = {"high": [], "medium": [], "needs_review": [], "low": []}
    for issue in issues:
        severity = str(issue.get("severity", "")).lower()
        if severity in grouped:
            grouped[severity].append(issue)
    return grouped


def _remaining_height(pdf: FPDF) -> float:
    return pdf.h - pdf.b_margin - pdf.get_y()


def _ensure_space(pdf: FPDF, height: float):
    if _remaining_height(pdf) < height:
        pdf.add_page()


def _text_height(pdf: FPDF, text: str, width: float, line_h: float) -> float:
    text = _safe(text)
    if width <= 10:
        return line_h
    # Prefer fpdf2's own dry-run line breaking so the card height matches
    # the real rendered multi_cell height. This prevents overlap when text
    # wraps differently than a simple string-width estimate predicts.
    x, y = pdf.get_x(), pdf.get_y()
    try:
        measured = pdf.multi_cell(width, line_h, text, dry_run=True, output="HEIGHT")
        pdf.set_xy(x, y)
        return max(float(measured), line_h)
    except Exception:
        pdf.set_xy(x, y)

    # Fallback: conservative string-width estimate.
    lines = 0
    for paragraph in text.split("\n"):
        w = max(pdf.get_string_width(paragraph), 1)
        lines += max(1, math.ceil(w / max(width - 2, 1)))
    return lines * line_h


def _safe_multi_cell(pdf: FPDF, w: float, h: float, txt: str, **kwargs):
    usable = pdf.w - pdf.l_margin - pdf.r_margin
    if w <= 0:
        w = usable
    w = max(12, min(w, usable))
    pdf.multi_cell(w, h, _safe(txt), **kwargs)


class ReportPDF(FPDF):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.nav_links = {}

    def footer(self):
        self.set_y(-9)
        self.set_font("Helvetica", "", 7)
        self.set_text_color(*COLORS["muted"])
        self.cell(0, 4, f"{self.page_no()}", align="C")
        self.set_text_color(*COLORS["ink"])

    def section(self, title: str, level: int = 0):
        """Create a visible section, a PDF outline/bookmark, and an internal-link target."""
        clean_title = _plain(title)
        try:
            self.start_section(clean_title, level=level)
        except Exception:
            pass
        try:
            if clean_title in self.nav_links:
                self.set_link(self.nav_links[clean_title], y=max(self.get_y() - 1, self.t_margin), page=self.page_no())
        except Exception:
            pass


def add_title_page(pdf: ReportPDF):
    # Match the original report-accessed_demo.pdf: clean centered title,
    # no header banner. Metadata/outline are still added separately for accessibility.
    pdf.section("Accessibility Report", 0)
    pdf.set_font("Helvetica", "B", 24)
    pdf.set_text_color(*COLORS["ink"])
    pdf.cell(0, 15, "Accessibility Report", align="C")
    pdf.ln(10)


def add_section_title(pdf: ReportPDF, title: str, level: int = 1):
    # Original visual style: simple bold section heading with generous spacing.
    # No decorative underline, so it stays close to report-accessed_demo.pdf.
    _ensure_space(pdf, 18)
    pdf.ln(7)
    pdf.section(title, level)
    pdf.set_font("Helvetica", "B", 14)
    pdf.set_text_color(*COLORS["ink"])
    pdf.cell(0, 8, title)
    pdf.ln(10)


def add_paragraph(pdf: FPDF, text: str):
    pdf.set_font("Helvetica", "", 10.5)
    pdf.set_text_color(*COLORS["ink"])
    _safe_multi_cell(pdf, 0, 8, text)
    pdf.ln(3)


def add_label_value(pdf: FPDF, label: str, value: str):
    left = pdf.l_margin
    width = pdf.w - pdf.l_margin - pdf.r_margin
    _ensure_space(pdf, 18)

    pdf.set_x(left)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(*COLORS["muted"])
    pdf.cell(32, 8, f"{label}:")

    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(*COLORS["ink"])
    pdf.set_x(left + 34)
    _safe_multi_cell(pdf, width - 34, 8, value)
    pdf.ln(2)


def add_overview(pdf: ReportPDF, meta: dict):
    # Match original: plain two-row overview, no container card.
    add_section_title(pdf, "Document Overview", 1)
    left = pdf.l_margin
    y = pdf.get_y()

    pdf.set_xy(left + 5, y + 4)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(*COLORS["muted"])
    pdf.cell(22, 6, "Title")

    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(*COLORS["ink"])
    pdf.cell(0, 6, _plain(meta.get("title")))

    pdf.set_xy(left + 5, y + 12)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(*COLORS["muted"])
    pdf.cell(22, 6, "Pages")

    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(*COLORS["ink"])
    pdf.cell(0, 6, _plain(meta.get("page_count")))

    pdf.set_y(y + 28)


def add_summary_row(pdf: FPDF, score: dict):
    breakdown = score.get("breakdown", {})

    items = [
        ("N/A", str(score.get("not_applicable", 0)), COLORS["na"]),
        ("NEEDS REVIEW", str(score.get("needs_review", 0)), COLORS["review"]),
        ("PASS", str(breakdown.get("pass", 0)), COLORS["pass"]),
        ("LOW", str(breakdown.get("low", 0)), COLORS["low"]),
        ("MEDIUM", str(breakdown.get("medium", 0)), COLORS["medium"]),
        ("HIGH", str(breakdown.get("high", 0)), COLORS["high"]),
    ]

    left = pdf.l_margin
    total_width = pdf.w - pdf.l_margin - pdf.r_margin
    gap = 3
    box_width = (total_width - 10 - gap * 5) / 6
    box_height = 23
    container_h = 43
    _ensure_space(pdf, container_h + 8)

    container_x = left
    container_y = pdf.get_y()
    container_w = total_width

    pdf.set_fill_color(*COLORS["panel"])
    pdf.set_draw_color(*COLORS["line"])
    pdf.rect(container_x, container_y, container_w, container_h, style="D")

    top_y = container_y + 4
    pdf.set_xy(container_x + 5, top_y)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(*COLORS["ink"])
    pdf.cell(60, 6, "Breakdown")

    badge_text = f"Evaluable Issues: {score.get('evaluable', 0)}"
    badge_h = 8
    cards_start_x = left + 5
    pass_x = cards_start_x + 2 * (box_width + gap)
    badge_x = pass_x
    badge_y = top_y - 0.5
    badge_w = (container_x + container_w - 5) - badge_x

    # Original-style badge position with outline-only drawing to avoid non-text contrast flags.
    pdf.set_fill_color(241, 245, 249)
    pdf.set_draw_color(*COLORS["line"])
    pdf.rect(badge_x, badge_y, badge_w, badge_h, style="D")
    pdf.set_xy(badge_x, badge_y + 1.6)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(*COLORS["ink"])
    pdf.cell(badge_w, 4, badge_text, align="C")

    row_y = container_y + 15
    for i, (label, value, color) in enumerate(items):
        x = left + 5 + i * (box_width + gap)
        # Original-style card grid, but outline-only so decorative backgrounds do not trigger non-text contrast flags.
        fill_key = label.lower().replace("needs review", "review").replace("n/a", "na")
        fill = SOFT_FILLS.get(fill_key, COLORS["paper"])
        pdf.set_fill_color(*fill)
        pdf.set_draw_color(*COLORS["line"])
        pdf.rect(x, row_y, box_width, box_height, style="D")

        pdf.set_xy(x, row_y + 4)
        pdf.set_font("Helvetica", "B", 6.7 if label == "NEEDS REVIEW" else 8)
        pdf.set_text_color(*color)
        pdf.cell(box_width, 4, label, align="C")

        pdf.set_xy(x, row_y + 12)
        pdf.set_font("Helvetica", "B", 12)
        pdf.set_text_color(*COLORS["ink"])
        pdf.cell(box_width, 5, value, align="C")

    pdf.set_text_color(*COLORS["ink"])
    pdf.set_y(container_y + container_h + 7)


def add_score_block(pdf: ReportPDF, score: dict):
    # Match original: centered score and grade, no surrounding score card.
    add_section_title(pdf, "Accessibility Score", 1)

    score_value = _plain(score.get("score"))
    grade = _plain(score.get("grade"))

    pdf.set_font("Helvetica", "B", 30)
    pdf.set_text_color(*COLORS["ink"])
    pdf.cell(0, 12, f"{score_value} / 100", align="C")
    pdf.ln(12)

    pdf.set_font("Helvetica", "", 12)
    pdf.set_text_color(*COLORS["muted"])
    pdf.cell(0, 8, f"Grade {grade}", align="C")
    pdf.ln(10)

    add_summary_row(pdf, score)
    pdf.set_text_color(*COLORS["ink"])

def add_scoring_methodology(pdf: ReportPDF):
    add_section_title(pdf, "How This Score Is Calculated", 1)

    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(*COLORS["ink"])
    _safe_multi_cell(
        pdf,
        0,
        6.5,
        "The accessibility score (0-100) reflects the percentage of evaluable WCAG 2.1 "
        "criteria that your document satisfies, weighted by violation severity. "
        "Criteria marked as Not Applicable or Needs Review are excluded from the calculation.",
    )
    pdf.ln(4)

    left = pdf.l_margin
    total_width = pdf.w - pdf.l_margin - pdf.r_margin
    gap = 4
    col1 = 34
    col2 = 24
    col3 = total_width - col1 - col2 - gap * 2

    rows = [
        ("PASS", "1.00", "No violation detected. Full credit.", COLORS["pass"]),
        ("LOW", "0.75", "Minor gap, mostly compliant.", COLORS["low"]),
        ("MEDIUM", "0.25", "Confirmed violation, moderate AT impact.", COLORS["medium"]),
        ("HIGH", "0.00", "Confirmed violation, severe AT impact.", COLORS["high"]),
    ]

    _ensure_space(pdf, 50)
    y = pdf.get_y()
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(*COLORS["muted"])
    pdf.set_xy(left, y)
    pdf.cell(col1, 7, "Severity")
    pdf.cell(col2, 7, "Weight", align="C")
    pdf.cell(col3, 7, "Meaning")
    pdf.ln(8)

    for label, weight, meaning, color in rows:
        y = pdf.get_y()
        fill_key = label.lower()
        pdf.set_fill_color(*SOFT_FILLS.get(fill_key, COLORS["paper"]))
        pdf.set_draw_color(*COLORS["line"])
        pdf.rect(left, y + 1, col1, 8, style="D")
        pdf.set_xy(left, y + 2.5)
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_text_color(*color)
        pdf.cell(col1, 5, label, align="C")

        pdf.set_xy(left + col1 + gap, y + 2)
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(*COLORS["ink"])
        pdf.cell(col2, 6, weight, align="C")

        pdf.set_xy(left + col1 + col2 + gap * 2, y + 2)
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(*COLORS["ink"])
        pdf.cell(col3, 6, meaning)
        pdf.ln(9)

    pdf.ln(1)
    # Keep the Grade Scale heading and cards together; otherwise the heading can
    # remain at the bottom of page 1 while the grade cards move to page 2.
    _ensure_space(pdf, 32)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(*COLORS["ink"])
    pdf.cell(0, 6, "Grade Scale")
    pdf.ln(7)

    grades = [
        ("A", "90-100", COLORS["pass"]),
        ("B", "75-89", COLORS["review"]),
        ("C", "50-74", COLORS["low"]),
        ("D", "25-49", COLORS["medium"]),
        ("F", "0-24", COLORS["high"]),
    ]

    grade_w = (total_width - gap * 4) / 5
    y = pdf.get_y()
    for i, (grade, rng, color) in enumerate(grades):
        x = left + i * (grade_w + gap)
        fill_key = {"A":"pass", "B":"review", "C":"low", "D":"medium", "F":"high"}.get(grade, "na")
        pdf.set_fill_color(*SOFT_FILLS.get(fill_key, COLORS["paper"]))
        pdf.set_draw_color(*COLORS["line"])
        pdf.rect(x, y, grade_w, 16, style="D")

        pdf.set_xy(x, y + 2)
        pdf.set_font("Helvetica", "B", 12)
        pdf.set_text_color(*color)
        pdf.cell(grade_w, 6, grade, align="C")

        pdf.set_xy(x, y + 9)
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_text_color(*COLORS["ink"])
        pdf.cell(grade_w, 5, rng, align="C")

    pdf.set_text_color(*COLORS["ink"])
    pdf.set_y(y + 22)


def add_navigation_note(pdf: ReportPDF):
    """Visible, nicer table of contents.

    We intentionally avoid the previous explanatory paragraph:
    "Report Navigation ..." because it looked like noisy body text in the report.
    The table of contents itself, the section headings, and the PDF outline/bookmarks
    still provide multiple ways to locate content.
    """
    add_section_title(pdf, "Table of Contents", 1)

    toc_items = [
        ("01", "Document Overview"),
        ("02", "Accessibility Score"),
        ("03", "How This Score Is Calculated"),
        ("04", "Executive Summary"),
        ("05", "Detailed Findings"),
    ]

    left = pdf.l_margin
    width = pdf.w - pdf.l_margin - pdf.r_margin
    row_h = 11
    gap = 2
    _ensure_space(pdf, len(toc_items) * (row_h + gap) + 6)

    for number, title in toc_items:
        link = pdf.nav_links.get(title)
        y = pdf.get_y()

        # Clean outlined row: close to the original minimal report style, but
        # more polished than a plain bullet list and fully high-contrast.
        pdf.set_draw_color(*COLORS["line"])
        pdf.set_fill_color(*COLORS["paper"])
        pdf.rect(left, y, width, row_h, style="D")

        pdf.set_xy(left + 5, y + 2.3)
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(*COLORS["review"])
        pdf.cell(12, 5, number)

        pdf.set_xy(left + 20, y + 2.3)
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(*COLORS["ink"])
        label = f"Go to {title}"
        if link is not None:
            pdf.cell(width - 25, 5, label, link=link)
        else:
            pdf.cell(width - 25, 5, label)

        pdf.set_y(y + row_h + gap)

    pdf.ln(3)


def add_issue_block(pdf: FPDF, issue: Dict[str, Any]):
    """Original-style issue card with safer spacing.

    v3 temporarily rendered Needs Review issues as plain flowing text to reduce
    resize warnings, but that made the detailed explanation area look worse.
    This version restores the nicer card design for all severities while making
    the card more tolerant of 200% resize checks:
    - larger line height
    - full-width value areas instead of cramped side-by-side rows
    - conservative height calculation using fpdf2 dry-run wrapping
    - long cards can fall back to a flowing layout rather than overlap
    """
    severity = _safe(issue.get("severity")).lower().replace("\u200b", "")
    style = SEVERITY_STYLES.get(severity, {"color": COLORS["strong_line"], "label": severity.upper()})

    left = pdf.l_margin
    width = pdf.w - pdf.l_margin - pdf.r_margin
    content_x = left + 9
    content_w = width - 18

    issue_text = _safe(issue.get("issue"))
    location_text = _safe(_format_location(issue.get("location", {})))
    recommendation_text = _safe(issue.get("recommendation"))

    line_h = 7.8
    label_h = 5.2
    field_gap = 2.2
    section_gap = 3.2
    title_h = 17
    bottom_pad = 8

    pdf.set_font("Helvetica", "", 10)
    h_issue = label_h + field_gap + max(line_h, _text_height(pdf, issue_text, content_w, line_h))
    h_loc = label_h + field_gap + max(line_h, _text_height(pdf, location_text, content_w, line_h))
    h_rec = label_h + field_gap + max(line_h, _text_height(pdf, recommendation_text, content_w, line_h))
    block_h = title_h + 5 + h_issue + section_gap + h_loc + section_gap + h_rec + bottom_pad
    block_h = max(block_h, 72)

    printable_h = pdf.h - pdf.t_margin - pdf.b_margin
    if block_h > printable_h - 8:
        # Very long text: keep the same typography, but let the content flow
        # across pages to avoid overlap/clipping.
        _ensure_space(pdf, 34)
        pdf.set_font("Helvetica", "B", 12)
        pdf.set_text_color(*COLORS["ink"])
        pdf.cell(0, 8, f"WCAG {_plain(issue.get('criterion'))} - {style['label']}")
        pdf.ln(9)
        for label, value in (("Issue", issue_text), ("Location", location_text), ("Recommendation", recommendation_text)):
            pdf.set_font("Helvetica", "B", 10)
            pdf.set_text_color(*COLORS["muted"])
            pdf.cell(0, 6, f"{label}:")
            pdf.ln(6)
            pdf.set_font("Helvetica", "", 10)
            pdf.set_text_color(*COLORS["ink"])
            _safe_multi_cell(pdf, 0, line_h, value)
            pdf.ln(3)
        pdf.ln(4)
        return

    _ensure_space(pdf, block_h + 8)
    start_y = pdf.get_y()

    # Card container: close to the original detailed boxes, but outline-only so
    # decorative graphics do not become 1.4.11 failures.
    pdf.set_fill_color(*COLORS["panel"])
    pdf.set_draw_color(*COLORS["line"])
    pdf.rect(left, start_y, width, block_h, style="D")

    # Colored severity bar, same visual idea as the original.
    pdf.set_fill_color(*style["color"])
    pdf.rect(left, start_y, 3, block_h, style="F")

    # Title row
    pdf.set_xy(left + 8, start_y + 5)
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(*COLORS["ink"])
    pdf.cell(width - 70, 6, f"WCAG {_plain(issue.get('criterion'))}")

    # Severity badge
    badge_w = 36 if severity != "needs_review" else 44
    badge_h = 7
    badge_x = left + width - badge_w - 6
    badge_y = start_y + 4.5
    fill_key = severity if severity in SOFT_FILLS else "na"
    pdf.set_fill_color(*SOFT_FILLS.get(fill_key, COLORS["paper"]))
    pdf.set_draw_color(*COLORS["line"])
    pdf.rect(badge_x, badge_y, badge_w, badge_h, style="D")
    pdf.set_xy(badge_x, badge_y + 1.55)
    pdf.set_font("Helvetica", "B", 7.2)
    pdf.set_text_color(*style["color"])
    pdf.cell(badge_w, 4, style["label"], align="C")

    divider_y = start_y + title_h
    pdf.set_draw_color(*COLORS["line"])
    pdf.line(left + 8, divider_y, left + width - 8, divider_y)

    y = divider_y + 5

    def add_field(label: str, value: str):
        nonlocal y
        pdf.set_xy(content_x, y)
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(*COLORS["muted"])
        pdf.cell(0, label_h, f"{label}:")
        y += label_h + field_gap

        pdf.set_xy(content_x, y)
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(*COLORS["ink"])
        _safe_multi_cell(pdf, content_w, line_h, value)
        y = pdf.get_y() + section_gap

    add_field("Issue", issue_text)
    add_field("Location", location_text)
    add_field("Recommendation", recommendation_text)

    pdf.set_text_color(*COLORS["ink"])
    pdf.set_y(max(y, start_y + block_h) + 5)


def _set_pdf_metadata(pdf: ReportPDF, meta: dict):
    title = _plain(meta.get("title"))
    if title == "N/A":
        title = "Document"
    report_title = f"AccessEd Accessibility Report - {title}"
    try:
        pdf.set_title(report_title)
        pdf.set_author("AccessEd")
        pdf.set_subject("PDF accessibility evaluation report")
        pdf.set_creator("AccessEd")
        pdf.set_keywords("accessibility, WCAG, PDF, AccessEd")
        pdf.set_lang("en-US")
    except Exception:
        pass


def _tag_pdf_with_pikepdf(path: str):
    """Best-effort tagged-PDF post-processing.

    Important pikepdf 10.x detail:
    Dictionary({...}) must receive normal string keys like "/Type", not
    pikepdf.Name objects. Passing Name objects can trigger this runtime error:
    "pikepdf.Object is not a Dictionary or Stream: cannot get key '/startswith'".

    This function preserves the visual FPDF output, then adds:
    - /MarkInfo /Marked true
    - /Lang
    - /ViewerPreferences /DisplayDocTitle true
    - a non-empty /StructTreeRoot
    - /Tabs /S on every page
    - page-level marked content sequences with MCID 0
    """
    try:
        import pikepdf
        from pikepdf import Dictionary, Array, String, Name
    except Exception:
        return

    tmp_path = path + ".tagged.tmp"

    with pikepdf.open(path, allow_overwriting_input=True) as doc:
        root = doc.Root

        # Catalog-level accessibility metadata.
        root["/Lang"] = String("en-US")
        root["/MarkInfo"] = Dictionary({"/Marked": True})
        root["/ViewerPreferences"] = Dictionary({"/DisplayDocTitle": True})

        try:
            info = doc.docinfo
            info["/Title"] = String("AccessEd Accessibility Report")
            info["/Author"] = String("AccessEd")
            info["/Subject"] = String("PDF accessibility evaluation report")
            info["/Creator"] = String("AccessEd")
        except Exception:
            pass

        try:
            with doc.open_metadata(set_pikepdf_as_editor=False) as meta:
                meta["dc:title"] = "AccessEd Accessibility Report"
                meta["dc:creator"] = ["AccessEd"]
                meta["pdf:Producer"] = "AccessEd"
        except Exception:
            pass

        struct_root = doc.make_indirect(Dictionary({
            "/Type": Name("/StructTreeRoot"),
            "/K": Array([]),
            "/RoleMap": Dictionary({
                "/Document": Name("/Document"),
                "/Sect": Name("/Sect"),
                "/P": Name("/P"),
            }),
        }))

        doc_elem = doc.make_indirect(Dictionary({
            "/Type": Name("/StructElem"),
            "/S": Name("/Document"),
            "/P": struct_root,
            "/K": Array([]),
        }))
        struct_root["/K"].append(doc_elem)

        parent_nums = Array([])

        for idx, page in enumerate(doc.pages):
            page_obj = page.obj
            page_obj["/StructParents"] = idx
            page_obj["/Tabs"] = Name("/S")

            page_elem = doc.make_indirect(Dictionary({
                "/Type": Name("/StructElem"),
                "/S": Name("/Sect"),
                "/P": doc_elem,
                "/Pg": page_obj,
                "/K": Array([Dictionary({
                    "/Type": Name("/MCR"),
                    "/Pg": page_obj,
                    "/MCID": 0,
                })]),
            }))
            doc_elem["/K"].append(page_elem)

            parent_nums.append(idx)
            parent_nums.append(Array([page_elem]))

            # Wrap the existing page content in a marked-content sequence.
            # This is intentionally page-level so it is robust with FPDF output.
            try:
                contents = page_obj.get("/Contents", None)
                if contents is None:
                    continue

                if isinstance(contents, pikepdf.Array):
                    raw_parts = []
                    for stream in contents:
                        try:
                            raw_parts.append(stream.read_bytes())
                        except Exception:
                            pass
                    raw = b"\n".join(raw_parts)
                else:
                    raw = contents.read_bytes()

                # Avoid wrapping twice if this function is called repeatedly.
                if b"/MCID 0" not in raw[:200]:
                    wrapped = b"/P <</MCID 0>> BDC\n" + raw + b"\nEMC\n"
                    page_obj["/Contents"] = doc.make_stream(wrapped)
            except Exception:
                # During development you can change this to raise RuntimeError.
                # Keeping generation resilient prevents user-facing crashes.
                pass

        struct_root["/ParentTree"] = doc.make_indirect(Dictionary({"/Nums": parent_nums}))
        struct_root["/ParentTreeNextKey"] = len(doc.pages)
        root["/StructTreeRoot"] = struct_root

        doc.save(tmp_path)

    os.replace(tmp_path, path)

def build_pdf_report(report: Dict[str, Any], output_path: str):
    meta = report.get("meta", {})
    score = report.get("score", {})
    issues = report.get("issues", [])

    detailed_issues = [
        issue for issue in issues
        if str(issue.get("severity", "")).lower() in {"high", "medium", "low", "needs_review"}
    ]
    grouped = _group_issues_by_severity(detailed_issues)

    pdf = ReportPDF()
    pdf.set_auto_page_break(auto=True, margin=16)
    pdf.set_left_margin(15)
    pdf.set_right_margin(15)
    pdf.set_top_margin(15)
    _set_pdf_metadata(pdf, meta)
    for title in [
        "Document Overview",
        "Accessibility Score",
        "How This Score Is Calculated",
        "Executive Summary",
        "Detailed Findings",
        "High Severity Issues",
        "Medium Severity Issues",
        "Low Severity Issues",
        "Needs Review",
    ]:
        try:
            link = pdf.add_link()
            # fpdf2 cannot place a link annotation whose target has no page yet.
            # Assign a safe temporary destination now; section() updates it to
            # the real page/Y position once that section is rendered.
            pdf.set_link(link, y=pdf.t_margin, page=1)
            pdf.nav_links[title] = link
        except Exception:
            pass

    pdf.add_page()
    add_title_page(pdf)
    add_overview(pdf, meta)
    add_score_block(pdf, score)
    add_scoring_methodology(pdf)
    add_navigation_note(pdf)

    pdf.add_page()
    add_section_title(pdf, "Executive Summary", 1)
    add_paragraph(
        pdf,
        f"This report contains {len(detailed_issues)} actionable issue(s). "
        "Checks marked as pass or not applicable are summarized above and are not listed in detail.",
    )

    add_section_title(pdf, "Detailed Findings", 1)

    severity_sections = [
        ("high", "High Severity Issues"),
        ("medium", "Medium Severity Issues"),
        ("low", "Low Severity Issues"),
        ("needs_review", "Needs Review"),
    ]

    any_issue = False
    for key, label in severity_sections:
        severity_issues = grouped[key]
        if not severity_issues:
            continue
        any_issue = True
        add_section_title(pdf, label, 2)
        for issue in severity_issues:
            add_issue_block(pdf, issue)

    if not any_issue:
        add_paragraph(pdf, "No actionable accessibility issues were found.")

    pdf.output(output_path)
    _tag_pdf_with_pikepdf(output_path)


# Wrapper used by the backend route.
def build_report_pdf(report_json: dict) -> bytes:
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        build_pdf_report(report_json, tmp_path)
        with open(tmp_path, "rb") as f:
            return f.read()
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
