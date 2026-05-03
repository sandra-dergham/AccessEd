from .scoring import compute_score
from pathlib import Path
from typing import Any, Dict, List
from fpdf import FPDF
import math


def build_report(document_meta: dict, issues: list[dict]) -> dict:
    return {
        "meta": document_meta,
        "score": compute_score(issues),
        "issues": issues
    }

def _safe(value: Any, default: str = "N/A") -> str:
    if value is None:
        return default

    text = str(value).strip()
    if not text:
        return default

    replacements = {
        "\u2014": "-",   # em dash
        "\u2013": "-",   # en dash
        "\u2018": "'",   # left single quote
        "\u2019": "'",   # right single quote
        "\u201c": '"',   # left double quote
        "\u201d": '"',   # right double quote
        "\u2026": "...", # ellipsis
        "\xa0": " ",     # non-breaking space
    }

    for bad, good in replacements.items():
        text = text.replace(bad, good)

    return text

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
    grouped = {
        "high": [],
        "medium": [],
        "needs_review": [],
        "low": [],
    }

    for issue in issues:
        severity = str(issue.get("severity", "")).lower()
        if severity in grouped:
            grouped[severity].append(issue)

    return grouped
def add_summary_row(pdf: FPDF, score: dict):
    breakdown = score.get("breakdown", {})

    items = [
         ("N/A", str(score.get("not_applicable", 0)), (248, 250, 252), (100, 116, 139)),
        ("NEEDS REVIEW", str(score.get("needs_review", 0)), (239, 246, 255), (37, 99, 235)),
        ("PASS", str(breakdown.get("pass", 0)), (236, 253, 245), (5, 150, 105)),
        ("LOW", str(breakdown.get("low", 0)), (254, 252, 232), (161, 98, 7)),
        ("MEDIUM", str(breakdown.get("medium", 0)), (255, 237, 213), (194, 65, 12)),
        ("HIGH", str(breakdown.get("high", 0)), (254, 226, 226), (185, 28, 28)),
    ]

    left = pdf.l_margin
    total_width = pdf.w - pdf.l_margin - pdf.r_margin
    gap = 3
    box_width = (total_width-10 - gap * 5) / 6
    box_height = 20

    start_y = pdf.get_y()
    container_x = left
    container_y = start_y
    container_w = total_width
    container_h = 38

    pdf.set_fill_color(250, 250, 252)
    pdf.set_draw_color(226, 232, 240)
    pdf.rect(container_x, container_y, container_w, container_h, style="DF")
    top_y = container_y + 4

    pdf.set_xy(container_x + 5, top_y)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(30, 41, 59)
    pdf.cell(60, 6, "Breakdown")

    badge_text = f"Evaluable Issues: {score.get('evaluable', 0)}"
    badge_h = 7
    badge_y = top_y - 0.5
    cards_start_x = left + 5
    pass_x = cards_start_x + 2 * (box_width + gap)   
    badge_x = pass_x
    badge_w = (container_x + container_w - 5) - badge_x

    pdf.set_fill_color(241, 245, 249)
    pdf.set_draw_color(226, 232, 240)
    pdf.rect(badge_x, badge_y, badge_w, badge_h, style="DF")

    pdf.set_xy(badge_x, badge_y + 1.2)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(71, 85, 105)
    pdf.cell(badge_w, 4, badge_text, align="C")

    #row
    row_y = container_y + 14

    for i, (label, value, bg_color, text_color) in enumerate(items):
        x = left+ 5 + i * (box_width + gap)

        pdf.set_fill_color(*bg_color)
        pdf.set_draw_color(226, 232, 240)
        pdf.rect(x, row_y, box_width, box_height, style="DF")

        pdf.set_xy(x, row_y + 3)
        pdf.set_font("Helvetica", "B", 7 if label == "NEEDS REVIEW" else 8)
        pdf.set_text_color(*text_color)
        pdf.cell(box_width, 4, label, align="C")

        pdf.set_xy(x, row_y + 10)
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(box_width, 5, value, align="C")

    pdf.set_text_color(0, 0, 0)
    pdf.set_y(container_y + container_h + 6)

class ReportPDF(FPDF):
    def footer(self):
        self.set_y(-8)
        self.set_font("Helvetica", "", 7)
        self.cell(0, 4, f"{self.page_no()}", align="C")


def add_title_page(pdf: FPDF):
    pdf.set_font("Helvetica", "B", 24)
    pdf.set_text_color(15, 23, 42)
    pdf.cell(0, 15, "Accessibility Report", align="C")
    pdf.ln(10)

def add_section_title(pdf: FPDF, title: str):
    pdf.ln(8)
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 8, title)
    pdf.ln(8)


def add_label_value(pdf: FPDF, label: str, value: str):
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(45, 8, f"{label}:")
    pdf.set_font("Helvetica", "", 11)
    pdf.multi_cell(0, 8, _safe(value))
    pdf.ln(1)


def add_paragraph(pdf: FPDF, text: str):
    pdf.set_font("Helvetica", "", 11)
    pdf.multi_cell(0, 7, _safe(text))
    pdf.ln(2)

def add_score_block(pdf: FPDF, score: dict):
    add_section_title(pdf, "Accessibility Score")

    pdf.set_font("Helvetica", "B", 30)
    pdf.set_text_color(15, 23, 42)
    pdf.cell(0, 12, f"{_safe(score.get('score'))} / 100", align="C")
    pdf.ln(12)

    pdf.set_font("Helvetica", "", 12)
    pdf.set_text_color(71, 85, 105)
    pdf.cell(0, 8, f"Grade {_safe(score.get('grade'))}", align="C")
    pdf.ln(10)

    add_summary_row(pdf, score)

    pdf.set_text_color(0, 0, 0)

def add_scoring_methodology(pdf: FPDF):
    add_section_title(pdf, "How This Score Is Calculated")

    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(71, 85, 105)
    pdf.multi_cell(0, 6,
        "The accessibility score (0-100) reflects the percentage of evaluable WCAG 2.1 "
        "criteria that your document satisfies, weighted by violation severity. "
        "Criteria marked as Not Applicable or Needs Review are excluded from the calculation."
    )
    pdf.ln(4)

    left = pdf.l_margin
    total_width = pdf.w - pdf.l_margin - pdf.r_margin
    gap = 4
    col_w = (total_width - gap * 3) / 4

    rows = [
        ("PASS",         "1.00", "No violation detected. Full credit.",          (236, 253, 245), (5, 150, 105)),
        ("LOW",          "0.75", "Minor gap, mostly compliant.",                 (254, 252, 232), (161, 98, 7)),
        ("MEDIUM",       "0.25", "Confirmed violation, moderate AT impact.",     (255, 237, 213), (194, 65, 12)),
        ("HIGH",         "0.00", "Confirmed violation, severe AT impact.",       (254, 226, 226), (185, 28, 28)),
    ]

    # Header
    y = pdf.get_y()
    headers = ["Severity", "Weight", "Meaning", ""]
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(71, 85, 105)
    for i, h in enumerate(headers[:3]):
        pdf.set_xy(left + i * (col_w + gap), y)
        pdf.cell(col_w, 6, h)
    pdf.ln(7)

    for label, weight, meaning, bg, fg in rows:
        y = pdf.get_y()

        # Badge
        pdf.set_fill_color(*bg)
        pdf.set_draw_color(226, 232, 240)
        pdf.rect(left, y + 1, col_w, 7, style="DF")
        pdf.set_xy(left, y + 2)
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_text_color(*fg)
        pdf.cell(col_w, 5, label, align="C")

        # Weight
        pdf.set_xy(left + col_w + gap, y + 2)
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(15, 23, 42)
        pdf.cell(col_w, 5, weight, align="C")

        # Meaning
        pdf.set_xy(left + 2 * (col_w + gap), y + 2)
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(71, 85, 105)
        pdf.cell(col_w * 2, 5, meaning)

        pdf.ln(9)

    # Grade scale
    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(15, 23, 42)
    pdf.cell(0, 6, "Grade Scale")
    pdf.ln(7)

    grades = [
        ("A", "90-100", (236, 253, 245), (5, 150, 105)),
        ("B", "75-89",  (239, 246, 255), (37, 99, 235)),
        ("C", "50-74",  (254, 252, 232), (161, 98, 7)),
        ("D", "25-49",  (255, 237, 213), (194, 65, 12)),
        ("F", "0-24",   (254, 226, 226), (185, 28, 28)),
    ]

    grade_w = (total_width - gap * 4) / 5
    y = pdf.get_y()
    for i, (grade, rng, bg, fg) in enumerate(grades):
        x = left + i * (grade_w + gap)
        pdf.set_fill_color(*bg)
        pdf.set_draw_color(226, 232, 240)
        pdf.rect(x, y, grade_w, 16, style="DF")

        pdf.set_xy(x, y + 2)
        pdf.set_font("Helvetica", "B", 12)
        pdf.set_text_color(*fg)
        pdf.cell(grade_w, 6, grade, align="C")

        pdf.set_xy(x, y + 9)
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(*fg)
        pdf.cell(grade_w, 4, rng, align="C")

    pdf.set_text_color(0, 0, 0)
    pdf.set_y(y + 22)

def add_overview(pdf: FPDF, meta: dict):
    add_section_title(pdf, "Document Overview")

    left = pdf.l_margin
    y = pdf.get_y()

    pdf.set_xy(left + 5, y + 4)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(71, 85, 105)
    pdf.cell(22, 6, "Title")

    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(15, 23, 42)
    pdf.cell(0, 6, _safe(meta.get("title")))

    pdf.set_xy(left + 5, y + 12)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(71, 85, 105)
    pdf.cell(22, 6, "Pages")

    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(15, 23, 42)
    pdf.cell(0, 6, _safe(meta.get("page_count")))

    pdf.set_text_color(0, 0, 0)
    pdf.set_y(y + 28)


def add_issue_block(pdf: FPDF, issue: Dict[str, Any]):
    severity = _safe(issue.get("severity")).lower()

    severity_styles = {
        "high": {
            "badge_bg": (254, 226, 226),
            "badge_text": (185, 28, 28),
            "accent": (239, 68, 68),
        },
        "medium": {
            "badge_bg": (255, 237, 213),
            "badge_text": (194, 65, 12),
            "accent": (249, 115, 22),
        },
        "low": {
            "badge_bg": (254, 252, 232),
            "badge_text": (161, 98, 7),
            "accent": (234, 179, 8),
        },
        "needs_review": {
            "badge_bg": (239, 246, 255),
            "badge_text": (37, 99, 235),
            "accent": (59, 130, 246),
        },
    }

    style = severity_styles.get(
        severity,
        {
            "badge_bg": (241, 245, 249),
            "badge_text": (71, 85, 105),
            "accent": (148, 163, 184),
        },
    )

    left = pdf.l_margin
    width = pdf.w - pdf.l_margin - pdf.r_margin
    start_y = pdf.get_y()
    line_h = 6
    content_x = left + 8
    content_w = width - 14

    def text_lines(value: str, available_w: float) -> int:
        text_width = pdf.get_string_width(value)
        return max(1, math.ceil(text_width / available_w))
    
    
    pdf.set_font("Helvetica", "", 10)
    label_w = 42
    field_gap = 3
    available_w = content_w - label_w - field_gap

    h_issue = text_lines(_safe(issue.get("issue")), available_w) * line_h
    h_loc = text_lines(_format_location(issue.get("location", {})), available_w) * line_h
    h_rec = text_lines(_safe(issue.get("recommendation")), available_w) * line_h
    block_h = 22 + h_issue + h_loc + h_rec + 16

    # Page break protection
    if start_y + block_h > pdf.h -10:
        pdf.add_page()
        start_y = pdf.get_y()

    # Card container
    pdf.set_fill_color(250, 250, 252)
    pdf.set_draw_color(226, 232, 240)
    pdf.rect(left, start_y, width, block_h, style="DF")

    # Accent bar
    pdf.set_fill_color(*style["accent"])
    pdf.rect(left, start_y, 3, block_h, style="F")

    # Title
    pdf.set_xy(left + 8, start_y + 5)
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(15, 23, 42)
    pdf.cell(90, 6, f"WCAG {_safe(issue.get('criterion'))}")

    # Severity badge
    badge_w = 28 if severity != "needs_review" else 38
    badge_h = 6
    badge_x = left + width - badge_w - 6
    badge_y = start_y + 4.5

    pdf.set_fill_color(*style["badge_bg"])
    pdf.set_draw_color(226, 232, 240)
    pdf.rect(badge_x, badge_y, badge_w, badge_h, style="DF")

    pdf.set_xy(badge_x, badge_y + 1.2)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(*style["badge_text"])
    pdf.cell(badge_w, 4, severity.replace("_", " ").upper(), align="C")

    # Divider
    divider_y = start_y + 14
    pdf.set_draw_color(226, 232, 240)
    pdf.line(left + 8, divider_y, left + width - 8, divider_y)

    # Content
    y = divider_y + 4

    def add_field(label: str, value: str):
        nonlocal y
        label_w = 42
        field_gap = 3

        pdf.set_xy(content_x, y)
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(71, 85, 105)
        pdf.cell(label_w, 6, f"{label}:")

        pdf.set_xy(content_x + label_w + field_gap, y)
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(15, 23, 42)
        pdf.multi_cell(content_w - label_w - field_gap, 6, value)

        y = pdf.get_y() + 1

    add_field("Issue", _safe(issue.get("issue")))
    add_field("Location", _format_location(issue.get("location", {})))
    add_field("Recommendation", _safe(issue.get("recommendation")))

    pdf.set_text_color(0, 0, 0)
    pdf.set_y(start_y + block_h + 5)


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
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_left_margin(15)
    pdf.set_right_margin(15)

    pdf.add_page()
    add_title_page(pdf)
    add_overview(pdf,meta)
    add_score_block(pdf,score)
    add_scoring_methodology(pdf)
    
    add_section_title(pdf, "Executive Summary")
    add_paragraph(
        pdf,
        f"This report contains {len(detailed_issues)} actionable issue(s). "
        "Checks marked as pass or not applicable are summarized above and are not listed in detail."
    )

    add_section_title(pdf, "Detailed Findings")

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
        
        pdf.ln(2)

        for issue in severity_issues:
            add_issue_block(pdf, issue)

    if not any_issue:
        add_paragraph(pdf, "No actionable accessibility issues were found.")

    pdf.output(output_path)

#wrapper
def build_report_pdf(report_json: dict) -> bytes:
    import tempfile, os
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