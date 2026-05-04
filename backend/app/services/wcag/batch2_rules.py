import re
from .issue import make_issue


def run_batch2_rules(document_model: dict) -> list[dict]:
    issues: list[dict] = []

    issues += check_info_relationships(document_model)   # 1.3.1
    issues += check_meaningful_sequence(document_model)  # 1.3.2
    issues += check_images_of_text(document_model)       # 1.4.5
    issues += check_page_titled(document_model)          # 2.4.2
    issues += check_link_purpose(document_model)         # 2.4.4
    issues += check_headings_labels(document_model)      # 2.4.6
    issues += check_language_of_page(document_model)     # 3.1.1
    issues += check_language_of_parts(document_model)    # 3.1.2

    issues += check_sensory_characteristics(document_model)  # 1.3.3
    issues += check_identify_input_purpose(document_model)   # 1.3.5
    issues += check_reflow(document_model)                   # 1.4.10
    issues += check_text_spacing(document_model)             # 1.4.12
    issues += check_bypass_blocks(document_model)            # 2.4.1
    issues += check_multiple_ways(document_model)            # 2.4.5

    return issues

def check_info_relationships(document_model: dict) -> list[dict]:
    """
    WCAG 1.3.1 Info and Relationships

    Strong PDF-oriented check:
    Flags the document when structural information is missing or appears too weak
    to make relationships (such as headings, lists, and tables) programmatically
    determinable.
    """
    doc = document_model.get("document", {})
    structure = doc.get("structure", {})

    has_tags = bool(structure.get("has_tags", False))
    errors = structure.get("validation", {}).get("errors", []) or []
    structure_tree = structure.get("tree") or []

    issues: list[dict] = []

    missing_struct_tree = any("StructTreeRoot" in str(e) for e in errors)

    empty_tree = has_tags and isinstance(structure_tree, list) and len(structure_tree) == 0

    meaningful_roles = {
        "Document", "Part", "Sect", "Div",
        "H1", "H2", "H3", "H4", "H5", "H6",
        "P", "L", "LI", "Lbl", "LBody",
        "Table", "TR", "TH", "TD",
        "Figure", "Link"
    }

    def tree_has_meaningful_role(nodes: list) -> bool:
        for node in nodes:
            if not isinstance(node, dict):
                continue

            role = node.get("role")
            if role in meaningful_roles:
                return True

            children = node.get("children", [])
            real_children = [child for child in children if isinstance(child, dict)]

            if tree_has_meaningful_role(real_children):
                return True

        return False

    has_meaningful_structure = tree_has_meaningful_role(structure_tree)

    if (
        (not has_tags)
        or missing_struct_tree
        or empty_tree
        or (has_tags and not has_meaningful_structure)
    ):
        issues.append(
            make_issue(
                criterion="1.3.1",
                issue="info_relationships",
                location={"scope": "document"},
                severity="high",
                recommendation=(
                    "Provide a valid tagged PDF structure so headings, lists, tables, "
                    "figures, and other relationships are programmatically determinable."
                ),
            )
        )

    return issues


def check_meaningful_sequence(document_model: dict) -> list[dict]:
    """
    WCAG 1.3.2 Meaningful Sequence

    Checks whether a reading order exists, references valid text blocks,
    covers the document text blocks, and avoids obvious sequencing problems.
    """
    doc = document_model.get("document", {})

    reading_order = doc.get("reading_order", {}).get("order", [])
    text_blocks = doc.get("text_blocks", [])

    issues: list[dict] = []

    if not reading_order:
        issues.append(
            make_issue(
                criterion="1.3.2",
                issue="meaningful_sequence",
                location={"scope": "document"},
                severity="medium",
                recommendation=(
                    "Ensure the PDF defines a logical reading order so assistive "
                    "technologies read the content in the correct sequence."
                ),
            )
        )
        return issues

    block_map = {
        block.get("id"): block
        for block in text_blocks
        if block.get("id") is not None
    }
    valid_block_ids = set(block_map.keys())

    invalid_ids = [block_id for block_id in reading_order if block_id not in valid_block_ids]
    if invalid_ids:
        issues.append(
            make_issue(
                criterion="1.3.2",
                issue="meaningful_sequence",
                location={"scope": "document"},
                severity="medium",
                recommendation=(
                    "Ensure the reading order references valid text blocks and "
                    "matches the document content."
                ),
            )
        )
        return issues

    missing_blocks = [block_id for block_id in valid_block_ids if block_id not in reading_order]
    if missing_blocks:
        issues.append(
            make_issue(
                criterion="1.3.2",
                issue="meaningful_sequence",
                location={"scope": "document"},
                severity="medium",
                recommendation=(
                    "Ensure all meaningful text blocks are included in the reading order."
                ),
            )
        )
        return issues

    if len(reading_order) != len(set(reading_order)):
        issues.append(
            make_issue(
                criterion="1.3.2",
                issue="meaningful_sequence",
                location={"scope": "document"},
                severity="medium",
                recommendation=(
                    "Ensure the reading order does not repeat text blocks."
                ),
            )
        )
        return issues

    previous_page = None
    previous_y = None

    for block_id in reading_order:
        block = block_map[block_id]
        page_index = block.get("page_index")
        bbox = block.get("bbox")

        if bbox is None or len(bbox) != 4:
            continue

        y_top = bbox[1]

        if previous_page is not None:
            if page_index < previous_page:
                issues.append(
                    make_issue(
                        criterion="1.3.2",
                        issue="meaningful_sequence",
                        location={"scope": "document"},
                        severity="medium",
                        recommendation=(
                            "Ensure the reading order follows the correct page sequence."
                        ),
                    )
                )
                return issues

            if page_index == previous_page and previous_y is not None and y_top < previous_y - 50:
                issues.append(
                    make_issue(
                        criterion="1.3.2",
                        issue="meaningful_sequence",
                        location={"scope": "document"},
                        severity="medium",
                        recommendation=(
                            "Ensure the reading order follows a logical top-to-bottom sequence."
                        ),
                    )
                )
                return issues

        previous_page = page_index
        previous_y = y_top

    return issues


def check_images_of_text(document_model: dict) -> list[dict]:
    """
    WCAG 1.4.5 Images of Text

    Flags images that appear to contain meaningful readable text based on OCR.
    Small decorative text or low-confidence OCR results are ignored.
    """
    doc = document_model.get("document", {})
    image_occurrences = doc.get("images", {}).get("occurrences", [])

    issues: list[dict] = []

    MIN_TEXT_LENGTH = 10
    MIN_CONFIDENCE = 60

    for image in image_occurrences:
        ocr_text = image.get("ocr_text")
        ocr_confidence = image.get("ocr_confidence")

        if not ocr_text:
            continue

        text = ocr_text.strip()

        if not text:
            continue

        if len(text) < MIN_TEXT_LENGTH:
            continue

        if ocr_confidence is not None and ocr_confidence < MIN_CONFIDENCE:
            continue

        issues.append(
            make_issue(
                criterion="1.4.5",
                issue="images_of_text",
                location={
                    "page": image.get("page_index"),
                    "image_id": image.get("id"),
                },
                severity="medium",
                recommendation=(
                    "Avoid embedding text inside images. Provide the text as "
                    "real selectable content whenever possible."
                ),
            )
        )

    return issues


def check_page_titled(document_model: dict) -> list[dict]:
    """
    WCAG 2.4.2 Page Titled

    Checks whether the document defines a meaningful title in its metadata.
    Flags missing, empty, or obviously generic titles.
    """
    doc = document_model.get("document", {})
    metadata = doc.get("metadata", {})

    title = metadata.get("title")
    issues: list[dict] = []

    normalized_title = str(title).strip() if title else ""

    generic_titles = {
        "untitled",
        "document",
        "pdf",
        "file",
        "scan",
        "scanned document",
        "default",
        "test",
    }

    lowered = normalized_title.lower()

    is_missing = not normalized_title
    is_too_short = len(normalized_title) < 3
    is_generic = (
        lowered in generic_titles
        or lowered.startswith("microsoft word")
        or lowered.startswith("adobe acrobat")
    )

    if is_missing or is_too_short or is_generic:
        issues.append(
            make_issue(
                criterion="2.4.2",
                issue="page_titled",
                location={"scope": "document"},
                severity="medium",
                recommendation=(
                    "Provide a clear, descriptive document title in the PDF metadata."
                ),
            )
        )

    return issues


def check_link_purpose(document_model: dict) -> list[dict]:
    """
    WCAG 2.4.4 Link Purpose (In Context)

    Checks whether visible link text appears descriptive enough.
    Flags links with missing, vague, extremely short, or raw-URL-only text.
    """
    doc = document_model.get("document", {})
    links = doc.get("links", [])
    text_spans = doc.get("text_spans", [])

    issues: list[dict] = []

    vague_texts = {
        "click here",
        "here",
        "more",
        "read more",
        "more info",
        "learn more",
        "details",
        "link",
        "this",
        "continue",
    }

    spans_by_page: dict[int, list[dict]] = {}
    for span in text_spans:
        page_index = span.get("page_index")
        spans_by_page.setdefault(page_index, []).append(span)

    def center_of_bbox(bbox: list[float]) -> tuple[float, float]:
        x0, y0, x1, y1 = bbox
        return ((x0 + x1) / 2, (y0 + y1) / 2)

    def point_in_bbox(px: float, py: float, bbox: list[float]) -> bool:
        x0, y0, x1, y1 = bbox
        return x0 <= px <= x1 and y0 <= py <= y1

    def looks_like_raw_url(text: str) -> bool:
        lowered = text.lower()
        return lowered.startswith("http://") or lowered.startswith("https://") or lowered.startswith("www.")

    for link in links:
        page_index = link.get("page_index")
        link_id = link.get("id")
        link_bbox = link.get("bbox")
        page_spans = spans_by_page.get(page_index, [])

        if not link_bbox or len(link_bbox) != 4:
            continue

        matched_texts: list[str] = []

        for span in page_spans:
            span_bbox = span.get("bbox")
            if not span_bbox or len(span_bbox) != 4:
                continue

            cx, cy = center_of_bbox(span_bbox)
            if point_in_bbox(cx, cy, link_bbox):
                txt = (span.get("text") or "").strip()
                if txt:
                    matched_texts.append(txt)

        link_text = " ".join(matched_texts).strip()
        normalized_link_text = link_text.lower()

        is_missing = not link_text
        is_vague = normalized_link_text in vague_texts
        is_too_short = 0 < len(link_text) < 3
        is_raw_url = looks_like_raw_url(link_text)

        if is_missing or is_vague or is_too_short or is_raw_url:
            issues.append(
                make_issue(
                    criterion="2.4.4",
                    issue="link_purpose",
                    location={
                        "page": page_index,
                        "link_id": link_id,
                    },
                    severity="medium",
                    recommendation=(
                        "Provide descriptive link text that clearly explains the destination or purpose."
                    ),
                )
            )

    return issues

def check_headings_labels(document_model: dict) -> list[dict]:
    """
    WCAG 2.4.6 Headings and Labels

    Checks whether the document appears to contain headings, either through
    heading tags in the structure tree or parser-detected heading candidates.
    """
    doc = document_model.get("document", {})
    structure = doc.get("structure", {})
    heading_candidates = doc.get("heading_candidates", [])

    issues: list[dict] = []

    heading_roles = {"H1", "H2", "H3", "H4", "H5", "H6"}

    def tree_has_heading(nodes: list) -> bool:
        for node in nodes:
            if not isinstance(node, dict):
                continue

            role = node.get("role")
            if role in heading_roles:
                return True

            children = node.get("children", [])
            real_children = [child for child in children if isinstance(child, dict)]

            if tree_has_heading(real_children):
                return True

        return False

    structure_tree = structure.get("tree") or []
    has_heading_tags = tree_has_heading(structure_tree)
    has_heading_candidates = bool(heading_candidates)

    if not has_heading_tags and not has_heading_candidates:
        issues.append(
            make_issue(
                criterion="2.4.6",
                issue="headings_labels",
                location={"scope": "document"},
                severity="medium",
                recommendation=(
                    "Provide descriptive headings and ensure they are properly "
                    "tagged to support document navigation."
                ),
            )
        )

    return issues

def check_language_of_page(document_model: dict) -> list[dict]:
    """
    WCAG 3.1.1 Language of Page

    Checks whether the document defines a valid default language.
    Uses parser-inferred language as a fallback signal.
    """
    doc = document_model.get("document", {})
    structure = doc.get("structure", {})

    lang = structure.get("lang")
    inferred_lang = doc.get("inferred_language")
    issues: list[dict] = []

    normalized_lang = str(lang).strip() if lang else ""
    lang_pattern = r"^[a-zA-Z]{2,3}(-[a-zA-Z]{2,4})?$"

    has_valid_declared_lang = bool(normalized_lang) and re.match(lang_pattern, normalized_lang) is not None
    has_inferred_lang = bool(inferred_lang)

    if not has_valid_declared_lang and not has_inferred_lang:
        issues.append(
            make_issue(
                criterion="3.1.1",
                issue="language_of_page",
                location={"scope": "document"},
                severity="medium",
                recommendation=(
                    "Specify a valid default document language (for example: en, ar, fr, en-US) "
                    "so assistive technologies can read the content correctly."
                ),
            )
        )

    return issues

def check_language_of_parts(document_model: dict) -> list[dict]:
    """
    WCAG 3.1.2 Language of Parts

    Checks whether longer text spans appear to use a language different from
    the document default language.
    Ignores very short spans, URLs, emails, and non-linguistic content.
    """
    doc = document_model.get("document", {})
    structure = doc.get("structure", {})
    text_spans = doc.get("text_spans", [])

    document_lang = structure.get("lang")
    inferred_lang = doc.get("inferred_language")
    issues: list[dict] = []

    # Prefer inferred language over embedded /Lang tag
    # Many PDFs have incorrect or missing /Lang — use content-based detection
    if inferred_lang:
        inferred_lang = str(inferred_lang).strip().lower().split("-")[0]
        if document_lang:
            document_lang = str(document_lang).strip().lower().split("-")[0]
            # If embedded lang conflicts with inferred, trust inferred
            if document_lang != inferred_lang:
                document_lang = inferred_lang
        else:
            document_lang = inferred_lang
    elif document_lang:
        document_lang = str(document_lang).strip().lower().split("-")[0]
    else:
        return issues

    issues: list[dict] = []
    MIN_TEXT_LENGTH = 20

    for span in text_spans:
        span_lang = span.get("detected_language")
        text = (span.get("text") or "").strip()

        if not span_lang or not text:
            continue

        if len(text) < MIN_TEXT_LENGTH:
            continue

        if not any(ch.isalpha() for ch in text):
            continue

        lowered_text = text.lower()
        if "http://" in lowered_text or "https://" in lowered_text or "www." in lowered_text or "@" in lowered_text:
            continue

        alpha_chars = [c for c in text if c.isalpha()]
        if alpha_chars and sum(1 for c in alpha_chars if c.isupper()) / len(alpha_chars) > 0.7:
            continue

        has_non_latin = any(ord(c) > 0x024F for c in text if c.isalpha())
        is_pure_ascii = all(ord(c) < 128 for c in text if c.isalpha())

        if is_pure_ascii and not has_non_latin:
            continue

        LANGUAGE_NAME_PATTERNS = {
            "arabic", "english", "french", "spanish", "german",
            "italian", "portuguese", "chinese", "japanese", "korean",
            "hindi", "turkish", "dutch", "russian", "persian"
        }
        words = set(lowered_text.split())
        if words.intersection(LANGUAGE_NAME_PATTERNS):
            continue

        span_lang = str(span_lang).strip().lower().split("-")[0]

        if span_lang != document_lang:
            issues.append(
                make_issue(
                    criterion="3.1.2",
                    issue="language_of_parts",
                    location={
                        "page": span.get("page_index"),
                        "span_id": span.get("id"),
                    },
                    severity="low",
                    recommendation=(
                        "Mark passages that differ from the document's default "
                        "language so assistive technologies can pronounce them correctly."
                    ),
                )
            )

    return issues


def check_sensory_characteristics(document_model: dict) -> list[dict]:
    """
    WCAG 1.3.3 Sensory Characteristics

    Heuristic check for instructional text that relies on color, shape,
    or visual position.
    """
    doc = document_model.get("document", {})
    text_spans = doc.get("text_spans", [])

    issues: list[dict] = []

    instruction_keywords = {
        "click", "select", "choose", "press", "enter",
        "fill", "see", "refer", "use", "open", "check"
    }

    sensory_keywords = {
        "left", "right", "above", "below", "top", "bottom",
        "red", "blue", "green", "yellow",
        "circle", "square", "triangle",
        "next to", "beside", "under", "over"
    }

    for span in text_spans:
        text = (span.get("text") or "").strip().lower()

        if not text:
            continue

        has_instruction = any(keyword in text for keyword in instruction_keywords)
        has_sensory = any(keyword in text for keyword in sensory_keywords)

        if has_instruction and has_sensory:
            issues.append(
                make_issue(
                    criterion="1.3.3",
                    issue="sensory_characteristics",
                    location={
                        "page": span.get("page_index"),
                        "span_id": span.get("id"),
                    },
                    severity="needs review",
                    recommendation=(
                        "Avoid instructions that rely only on sensory cues such as "
                        "color, shape, or visual position. Provide a more explicit description."
                    ),
                )
            )

    return issues


def check_identify_input_purpose(document_model: dict) -> list[dict]:
    """
    WCAG 1.3.5 Identify Input Purpose

    Heuristic check for form fields whose purpose is not programmatically
    identifiable because the field name is missing, too short, or generic.
    """
    doc = document_model.get("document", {})
    form_fields = doc.get("form_fields", [])

    issues: list[dict] = []

    generic_names = {
        "field",
        "input",
        "textbox",
        "text",
        "box",
        "form",
        "untitled",
        "unknown",
        "default",
    }

    generic_pattern = re.compile(r"^(field|text|input|textbox|box)[\s_-]?\d*$", re.IGNORECASE)

    for field in form_fields:
        field_name = field.get("name")
        normalized_name = str(field_name).strip() if field_name else ""

        lowered = normalized_name.lower()

        is_missing = not normalized_name
        is_too_short = 0 < len(normalized_name) < 3
        is_generic = lowered in generic_names or generic_pattern.match(normalized_name) is not None

        if is_missing or is_too_short or is_generic:
            issues.append(
                make_issue(
                    criterion="1.3.5",
                    issue="identify_input_purpose",
                    location={
                        "page": field.get("page_index"),
                        "field_id": field.get("id"),
                    },
                    severity="needs review",
                    recommendation=(
                        "Ensure each form field has a meaningful programmatic name or label "
                        "that clearly identifies its purpose."
                    ),
                )
            )

    return issues


def check_reflow(document_model: dict) -> list[dict]:
    """
    WCAG 1.4.10 Reflow

    Heuristic check for wide text blocks that may indicate fixed-layout content
    requiring horizontal scrolling in narrow viewports.
    """
    doc = document_model.get("document", {})
    pages = doc.get("pages", [])
    text_blocks = doc.get("text_blocks", [])

    issues: list[dict] = []

    page_widths = {
        page.get("page_index"): page.get("width", 0)
        for page in pages
    }

    suspicious_blocks = []

    for block in text_blocks:
        bbox = block.get("bbox")
        page_index = block.get("page_index")
        text = (block.get("text") or "").strip()

        if not bbox or len(bbox) != 4:
            continue

        page_width = page_widths.get(page_index, 0)
        if not page_width:
            continue

        if len(text) < 40:
            continue

        x0, y0, x1, y1 = bbox
        block_width = x1 - x0
        width_ratio = block_width / page_width

        if width_ratio >= 0.9:
            suspicious_blocks.append(
                {
                    "page": page_index,
                    "block_id": block.get("id"),
                }
            )

    if len(suspicious_blocks) >= 2:
        first = suspicious_blocks[0]
        issues.append(
            make_issue(
                criterion="1.4.10",
                issue="reflow",
                location={
                    "page": first.get("page"),
                    "block_id": first.get("block_id"),
                },
                severity="needs review",
                recommendation=(
                    "Ensure content can adapt to narrower viewports without requiring "
                    "horizontal scrolling or fixed-width layout."
                ),
            )
        )

    return issues


def check_text_spacing(document_model: dict) -> list[dict]:
    """
    WCAG 1.4.12 Text Spacing

    Heuristic check for tightly packed text that may not tolerate increased
    spacing well. Compares nearby spans that appear to belong to the same
    text flow.
    """
    doc = document_model.get("document", {})
    text_spans = doc.get("text_spans", [])

    issues: list[dict] = []
    spans_by_page: dict[int, list[dict]] = {}

    for span in text_spans:
        page_index = span.get("page_index")
        spans_by_page.setdefault(page_index, []).append(span)

    for page_index, spans in spans_by_page.items():
        filtered_spans = []
        for span in spans:
            bbox = span.get("bbox")
            text = (span.get("text") or "").strip()
            font_size = span.get("font", {}).get("size", 0)

            if not bbox or len(bbox) != 4:
                continue
            if not text or len(text) < 10:
                continue
            if not font_size:
                continue

            filtered_spans.append(span)

        spans_sorted = sorted(filtered_spans, key=lambda s: s["bbox"][1])

        suspicious_pairs = 0
        first_suspicious_span_id = None

        for i in range(len(spans_sorted) - 1):
            current = spans_sorted[i]
            next_span = spans_sorted[i + 1]

            curr_bbox = current["bbox"]
            next_bbox = next_span["bbox"]

            curr_x = curr_bbox[0]
            next_x = next_bbox[0]

            curr_y = curr_bbox[1]
            next_y = next_bbox[1]

            curr_font_size = current.get("font", {}).get("size", 0)
            next_font_size = next_span.get("font", {}).get("size", 0)

            if not curr_font_size or not next_font_size:
                continue

            same_column = abs(curr_x - next_x) <= 40
            similar_font = abs(curr_font_size - next_font_size) <= 2

            if not same_column or not similar_font:
                continue

            line_gap = abs(next_y - curr_y)

            if line_gap < curr_font_size * 1.1:
                suspicious_pairs += 1
                if first_suspicious_span_id is None:
                    first_suspicious_span_id = current.get("id")

        if suspicious_pairs >= 2:
            issues.append(
                make_issue(
                    criterion="1.4.12",
                    issue="text_spacing",
                    location={
                        "page": page_index,
                        "span_id": first_suspicious_span_id,
                    },
                    severity="needs review",
                    recommendation=(
                        "Ensure text spacing can be increased without loss of "
                        "content or overlap, especially for tightly packed text."
                    ),
                )
            )

    return issues


def check_bypass_blocks(document_model: dict) -> list[dict]:
    """
    WCAG 2.4.1 Bypass Blocks

    PDF-adapted heuristic:
    Checks whether longer documents provide a navigation mechanism such as
    bookmarks, internal links, or a meaningful heading structure that helps
    users bypass repeated or non-essential content.
    """
    doc = document_model.get("document", {})
    pages = doc.get("pages", [])
    links = doc.get("links", [])
    bookmarks = doc.get("bookmarks", [])
    structure = doc.get("structure", {})

    issues: list[dict] = []

    page_count = len(pages)
    if page_count < 3:
        return issues

    has_bookmarks = bool(bookmarks)

    def is_internal_link(link: dict) -> bool:
        target = str(link.get("target") or "").lower()
        uri = str(link.get("uri") or "").lower()
        link_type = str(link.get("type") or "").lower()

        return (
            link_type == "internal"
            or target.startswith("#")
            or "page=" in target
            or "dest" in target
            or target.startswith("page")
            or uri.startswith("#")
        )

    has_internal_links = any(is_internal_link(link) for link in links)

    heading_roles = {"H1", "H2", "H3", "H4", "H5", "H6"}

    def tree_has_heading(nodes: list) -> bool:
        for node in nodes:
            if not isinstance(node, dict):
                continue

            if node.get("role") in heading_roles:
                return True

            children = node.get("children", [])
            real_children = [child for child in children if isinstance(child, dict)]

            if tree_has_heading(real_children):
                return True

        return False

    structure_tree = structure.get("tree") or []
    has_heading_navigation = tree_has_heading(structure_tree)

    if not has_bookmarks and not has_internal_links and not has_heading_navigation:
        issues.append(
            make_issue(
                criterion="2.4.1",
                issue="bypass_blocks",
                location={"scope": "document"},
                severity="needs review",
                recommendation=(
                    "Provide navigation aids such as bookmarks, a table of contents, "
                    "internal links, or a clear heading structure so users can move "
                    "through the document efficiently."
                ),
            )
        )

    return issues


def check_multiple_ways(document_model: dict) -> list[dict]:
    """
    WCAG 2.4.5 Multiple Ways

    PDF-adapted heuristic:
    Checks whether longer documents provide at least two distinct navigation
    methods, such as bookmarks, internal links, or heading structure.
    """
    doc = document_model.get("document", {})
    pages = doc.get("pages", [])
    links = doc.get("links", [])
    bookmarks = doc.get("bookmarks", [])
    structure = doc.get("structure", {})

    issues: list[dict] = []

    page_count = len(pages)
    if page_count < 3:
        return issues

    has_bookmarks = bool(bookmarks)

    def is_internal_link(link: dict) -> bool:
        target = str(link.get("target") or "").lower()
        uri = str(link.get("uri") or "").lower()
        link_type = str(link.get("type") or "").lower()

        return (
            link_type == "internal"
            or target.startswith("#")
            or "page=" in target
            or "dest" in target
            or target.startswith("page")
            or uri.startswith("#")
        )

    has_internal_links = any(is_internal_link(link) for link in links)

    heading_roles = {"H1", "H2", "H3", "H4", "H5", "H6"}

    def tree_has_heading(nodes: list) -> bool:
        for node in nodes:
            if not isinstance(node, dict):
                continue

            if node.get("role") in heading_roles:
                return True

            children = node.get("children", [])
            real_children = [child for child in children if isinstance(child, dict)]

            if tree_has_heading(real_children):
                return True

        return False

    structure_tree = structure.get("tree") or []
    has_heading_navigation = tree_has_heading(structure_tree)

    navigation_methods = 0
    if has_bookmarks:
        navigation_methods += 1
    if has_internal_links:
        navigation_methods += 1
    if has_heading_navigation:
        navigation_methods += 1

    if navigation_methods < 2:
        issues.append(
            make_issue(
                criterion="2.4.5",
                issue="multiple_ways",
                location={"scope": "document"},
                severity="needs review",
                recommendation=(
                    "Provide at least two ways to locate content in longer documents, "
                    "such as bookmarks, internal links, or a clear heading structure."
                ),
            )
        )

    return issues