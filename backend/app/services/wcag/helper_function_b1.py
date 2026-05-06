from typing import Any, Dict, List, Optional, Tuple
import fitz
import numpy as np
import re
from .issue import make_issue
import math
import pikepdf
import os
import json
import tempfile
import subprocess
from pathlib import Path



def bbox_width(bbox: List[float]) -> float:
    return max(0.0, bbox[2] - bbox[0])


def bbox_height(bbox: List[float]) -> float:
    return max(0.0, bbox[3] - bbox[1])


def scale_bbox_from_center(
    bbox: List[float],
    scale_x: float = 2.0,
    scale_y: float = 2.0
) -> List[float]:
    x0, y0, x1, y1 = bbox
    cx = (x0 + x1) / 2.0
    cy = (y0 + y1) / 2.0

    new_w = (x1 - x0) * scale_x
    new_h = (y1 - y0) * scale_y

    return [
        cx - new_w / 2.0,
        cy - new_h / 2.0,
        cx + new_w / 2.0,
        cy + new_h / 2.0,
    ]


def bbox_contains(outer: List[float], inner: List[float], margin: float = 0.0) -> bool:
    return (
        outer[0] - margin <= inner[0] and
        outer[1] - margin <= inner[1] and
        outer[2] + margin >= inner[2] and
        outer[3] + margin >= inner[3]
    )


def center_of_bbox(b: List[float]) -> Tuple[float, float]:
    x0, y0, x1, y1 = b
    return ((x0 + x1) / 2.0, (y0 + y1) / 2.0)


def bbox_area(bbox: Optional[List[float]]) -> float:
    if not bbox or len(bbox) != 4:
        return 0.0
    x0, y0, x1, y1 = bbox
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)


def bbox_intersection(b1: List[float], b2: List[float]) -> Optional[List[float]]:
    x0 = max(b1[0], b2[0])
    y0 = max(b1[1], b2[1])
    x1 = min(b1[2], b2[2])
    y1 = min(b1[3], b2[3])

    if x1 <= x0 or y1 <= y0:
        return None
    return [x0, y0, x1, y1]


def bbox_expand(bbox: List[float], margin: float) -> List[float]:
    x0, y0, x1, y1 = bbox
    return [x0 - margin, y0 - margin, x1 + margin, y1 + margin]


def intersection_ratio_of_span(span_bbox: List[float], other_bbox: List[float]) -> float:
    inter = bbox_intersection(span_bbox, other_bbox)
    if inter is None:
        return 0.0
    span_a = bbox_area(span_bbox)
    if span_a <= 0:
        return 0.0
    return bbox_area(inter) / span_a


def bbox_intersects(b1: List[float], b2: List[float], margin: float = 0.0) -> bool:
    bb1 = bbox_expand(b1, margin) if margin > 0 else b1
    return bbox_intersection(bb1, b2) is not None


def pdfminer_bbox_to_pymupdf_bbox(bbox_pdfminer: List[float], page_height: float) -> List[float]:
    """
    pdfminer bbox: [x0, y0, x1, y1] with origin bottom-left.
    Convert to PyMuPDF-like: y grows downward (top-left style).
    """
    x0, y0, x1, y1 = bbox_pdfminer
    new_y0 = page_height - y1
    new_y1 = page_height - y0
    return [float(x0), float(new_y0), float(x1), float(new_y1)]


def srgb_channel_to_linear(c: float) -> float:
    c = c / 255.0
    if c <= 0.04045:
        return c / 12.92
    return ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(rgb: List[int]) -> float:
    r, g, b = rgb
    R = srgb_channel_to_linear(r)
    G = srgb_channel_to_linear(g)
    B = srgb_channel_to_linear(b)
    return 0.2126 * R + 0.7152 * G + 0.0722 * B


def contrast_ratio(fg_rgb: List[int], bg_rgb: List[int]) -> float:
    L1 = relative_luminance(fg_rgb)
    L2 = relative_luminance(bg_rgb)
    lighter = max(L1, L2)
    darker = min(L1, L2)
    return (lighter + 0.05) / (darker + 0.05)


def colors_are_distinct(c1: list[int], c2: list[int], threshold: float = 80.0) -> bool:
    """
    Determine whether two colors are clearly distinct.
    Uses Euclidean RGB distance.
    """
    if not c1 or not c2 or len(c1) < 3 or len(c2) < 3:
        return False

    r1, g1, b1 = c1[:3]
    r2, g2, b2 = c2[:3]

    dist = math.sqrt(
        (r1 - r2) ** 2 +
        (g1 - g2) ** 2 +
        (b1 - b2) ** 2
    )
    return dist >= threshold


def similar_font_properties(
    font1: Dict[str, Any],
    font2: Dict[str, Any],
    size_tol: float = 1.0
) -> bool:
    same_name = font1.get("name") == font2.get("name")
    similar_size = abs(float(font1.get("size", 0.0)) - float(font2.get("size", 0.0))) <= size_tol

    flags1 = font1.get("flags")
    flags2 = font2.get("flags")

    flags_close = True
    if flags1 is not None and flags2 is not None:
        flags_close = flags1 == flags2

    return same_name and similar_size and flags_close


def is_url_like_text(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return False
    return (
        "http://" in t
        or "https://" in t
        or t.startswith("www.")
        or "@" in t
    )


def is_same_line(span1: Dict[str, Any], span2: Dict[str, Any], y_tol: float = 6.0) -> bool:
    l1 = span1.get("layout", {})
    l2 = span2.get("layout", {})

    if (
        l1.get("block_index") is not None and
        l1.get("line_index") is not None and
        l2.get("block_index") is not None and
        l2.get("line_index") is not None
    ):
        return (
            l1.get("block_index") == l2.get("block_index")
            and l1.get("line_index") == l2.get("line_index")
            and span1.get("page_index") == span2.get("page_index")
        )

    b1 = span1.get("bbox")
    b2 = span2.get("bbox")
    if not b1 or not b2:
        return False

    _, c1y = center_of_bbox(b1)
    _, c2y = center_of_bbox(b2)
    return abs(c1y - c2y) <= y_tol


def _horizontal_gap(b1: list[float], b2: list[float]) -> float:
    return max(b1[0] - b2[2], b2[0] - b1[2], 0.0)


def _vertical_gap(b1: list[float], b2: list[float]) -> float:
    return max(b1[1] - b2[3], b2[1] - b1[3], 0.0)


def _sort_spans_reading_order(spans: list[dict]) -> list[dict]:
    def sort_key(span: dict):
        layout = span.get("layout", {})
        bbox = span.get("bbox", [0.0, 0.0, 0.0, 0.0])

        block_index = layout.get("block_index")
        line_index = layout.get("line_index")

        if block_index is not None and line_index is not None:
            return (0, block_index, line_index, bbox[0], bbox[1])

        return (1, bbox[1], bbox[0], bbox[3], bbox[2])

    return sorted(spans, key=sort_key)


def _union_bboxes(boxes: list[list[float]]) -> list[float] | None:
    if not boxes:
        return None
    return [
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    ]


def _normalize_repeat_text(text: str) -> str:
    return " ".join((text or "").strip().lower().split())


def extract_widgets(page: fitz.Page, page_index: int) -> List[Dict[str, Any]]:
    widgets = []
    w_counter = 0

    try:
        ws = page.widgets()
    except Exception:
        ws = []

    if not ws:
        return widgets

    for w in ws:
        try:
            rect = w.rect
        except Exception:
            continue

        try:
            flags = int(w.field_flags)
        except Exception:
            flags = 0

        try:
            widget_type = str(w.field_type)
        except Exception:
            widget_type = None

        try:
            field_name = w.field_name
        except Exception:
            field_name = None

        is_read_only = bool(flags & 1)

        widgets.append({
            "id": f"widget_p{page_index}_{w_counter}",
            "page_index": page_index,
            "bbox": [float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)],
            "field_name": field_name,
            "field_type": widget_type,
            "field_flags": flags,
            "ui_state": "inactive" if is_read_only else "active"
        })
        w_counter += 1

    return widgets


def has_underline_graphic(
    span_bbox: List[float],
    page_graphics: List[Dict[str, Any]],
    vertical_tol: float = 3.0,
    max_line_height: float = 3.0,
    min_width_ratio: float = 0.6,
) -> bool:
    sx0, sy0, sx1, sy1 = span_bbox
    span_w = bbox_width(span_bbox)

    for g in page_graphics:
        gb = g.get("bbox")
        if not gb:
            continue

        gx0, gy0, gx1, gy1 = gb
        gw = bbox_width(gb)
        gh = bbox_height(gb)

        if gh > max_line_height:
            continue
        if gw < span_w * min_width_ratio:
            continue

        horizontally_aligned = not (gx1 < sx0 or gx0 > sx1)
        if not horizontally_aligned:
            continue

        if abs(gy0 - sy1) <= vertical_tol or abs(((gy0 + gy1) / 2.0) - sy1) <= vertical_tol:
            return True

    return False


def has_enclosing_box_cue(
    span_bbox: List[float],
    page_graphics: List[Dict[str, Any]],
    margin: float = 2.0,
    max_extra_scale: float = 1.8,
) -> bool:
    span_w = bbox_width(span_bbox)
    span_h = bbox_height(span_bbox)

    for g in page_graphics:
        gb = g.get("bbox")
        if not gb:
            continue

        if bbox_contains(gb, span_bbox, margin=margin):
            gw = bbox_width(gb)
            gh = bbox_height(gb)

            tight_w = gw <= span_w * max_extra_scale
            tight_h = gh <= span_h * max_extra_scale

            if tight_w and tight_h:
                return True

    return False


def span_intersects_any_link(
    span_bbox: List[float],
    links: List[Dict[str, Any]],
    margin: float = 2.0
) -> Optional[Dict[str, Any]]:
    for link in links:
        lb = link.get("bbox")
        if not lb:
            continue
        if bbox_intersects(span_bbox, lb, margin=margin):
            return link
    return None


def collect_same_line_non_link_neighbors(
    target_span: Dict[str, Any],
    text_spans: List[Dict[str, Any]],
    page_links: List[Dict[str, Any]],
    max_horizontal_distance: float = 220.0,
) -> List[Dict[str, Any]]:
    out = []
    tb = target_span.get("bbox")
    if not tb:
        return out

    for other in text_spans:
        if other.get("id") == target_span.get("id"):
            continue
        if other.get("page_index") != target_span.get("page_index"):
            continue

        ob = other.get("bbox")
        if not ob:
            continue

        if not is_same_line(target_span, other):
            continue

        if span_intersects_any_link(ob, page_links, margin=2.0):
            continue

        out.append(other)

    return out


AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".wmv", ".mkv", ".webm", ".mpeg", ".mpg"}


TRANSCRIPT_POSITIVE_RE = re.compile(
    r"\b(?:"
    r"transcript(?:\s+(?:is\s+)?(?:available|provided|included))?"
    r"|full transcript"
    r"|see transcript"
    r"|transcript below"
    r"|transcript attached"
    r"|text transcript"
    r"|written transcript"
    r")\b",
    re.I,
)

TRANSCRIPT_NEGATIVE_RE = re.compile(
    r"\b(?:"
    r"no transcript"
    r"|without transcript"
    r"|transcript(?:\s+is)?\s+not\s+available"
    r"|transcript(?:\s+is)?\s+unavailable"
    r"|transcript(?:\s+is)?\s+not\s+provided"
    r"|transcript missing"
    r")\b",
    re.I,
)

CAPTION_POSITIVE_RE = re.compile(
    r"\b(?:"
    r"captioned"
    r"|captions?(?:\s+(?:are\s+|is\s+)?(?:available|provided|included))?"
    r"|closed captions?"
    r"|open captions?"
    r"|subtitles?(?:\s+(?:are\s+|is\s+)?(?:available|provided|included))?"
    r"|cc\b"
    r")\b",
    re.I,
)

CAPTION_NEGATIVE_RE = re.compile(
    r"\b(?:"
    r"no captions?"
    r"|without captions?"
    r"|captions?(?:\s+are|\s+is)?\s+not\s+available"
    r"|captions?(?:\s+are|\s+is)?\s+unavailable"
    r"|not captioned"
    r"|no subtitles?"
    r"|without subtitles?"
    r"|subtitles?(?:\s+are|\s+is)?\s+not\s+available"
    r"|subtitles?(?:\s+are|\s+is)?\s+unavailable"
    r")\b",
    re.I,
)

AUDIO_DESC_POSITIVE_RE = re.compile(
    r"\b(?:"
    r"audio description(?:\s+(?:is\s+)?(?:available|provided|included))?"
    r"|described video"
    r"|descriptive audio"
    r"|audio-described"
    r"|audio described"
    r")\b",
    re.I,
)

AUDIO_DESC_NEGATIVE_RE = re.compile(
    r"\b(?:"
    r"no audio description"
    r"|without audio description"
    r"|audio description(?:\s+is)?\s+not\s+available"
    r"|audio description(?:\s+is)?\s+unavailable"
    r"|not described"
    r")\b",
    re.I,
)

MEDIA_ALT_POSITIVE_RE = re.compile(
    r"\b(?:"
    r"media alternative"
    r"|text alternative"
    r"|alternative for time-based media"
    r"|alternative version"
    r")\b",
    re.I,
)

MEDIA_ALT_NEGATIVE_RE = re.compile(
    r"\b(?:"
    r"no media alternative"
    r"|without media alternative"
    r"|media alternative(?:\s+is)?\s+not\s+available"
    r"|media alternative(?:\s+is)?\s+unavailable"
    r"|no text alternative"
    r"|without text alternative"
    r"|text alternative(?:\s+is)?\s+not\s+available"
    r"|text alternative(?:\s+is)?\s+unavailable"
    r")\b",
    re.I,
)

LIVE_RE = re.compile(
    r"\b(?:live|livestream|live stream|webcast|broadcast)\b",
    re.I,
)


LIVE_KEYWORDS = [
    "live",
    "livestream",
    "webcast",
    "broadcast",
]

ALT_TEXT_KEYWORDS = [
    "transcript",
    "media alternative",
    "text alternative",
    "alternative for time-based media",
    "audio description",
    "described video",
]


def classify_by_filename(filename: Optional[str]) -> str:
    name = (filename or "").lower()
    for ext in AUDIO_EXTS:
        if name.endswith(ext):
            return "audio_only"
    for ext in VIDEO_EXTS:
        if name.endswith(ext):
            return "video_only"
    return "unknown"


def _classify_media_kind(filename: Optional[str], mime_type: Optional[str] = None) -> str:
    name = (filename or "").lower()
    mime = (mime_type or "").lower()

    if any(name.endswith(ext) for ext in AUDIO_EXTS) or mime.startswith("audio/"):
        return "audio"

    if any(name.endswith(ext) for ext in VIDEO_EXTS) or mime.startswith("video/"):
        return "video"

    return "unknown"


def safe_pdf_name(value) -> Optional[str]:
    if value is None:
        return None
    try:
        s = str(value)
        if s.startswith("/"):
            return s[1:]
        return s
    except Exception:
        return None


def run_ffprobe(media_path: str) -> Dict[str, Any]:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-show_streams",
                "-show_format",
                "-of", "json",
                media_path,
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if result.returncode != 0:
            return {"ok": False, "reason": "ffprobe_failed"}

        data = json.loads(result.stdout)
        streams = data.get("streams", [])

        has_audio = any(s.get("codec_type") == "audio" for s in streams)
        has_video = any(s.get("codec_type") == "video" for s in streams)
        has_subtitle = any(s.get("codec_type") == "subtitle" for s in streams)

        if has_audio and has_video:
            media_class = "audio_video"
        elif has_audio:
            media_class = "audio_only"
        elif has_video:
            media_class = "video_only"
        else:
            media_class = "unknown"

        return {
            "ok": True,
            "has_audio": has_audio,
            "has_video": has_video,
            "has_subtitle": has_subtitle,
            "media_class": media_class,
            "streams": streams,
            "format": data.get("format", {}),
        }
    except Exception as e:
        return {"ok": False, "reason": f"{type(e).__name__}: {e}"}


def extract_media_occurrences(doc: fitz.Document, text_spans: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    media_occurrences: List[Dict[str, Any]] = []

    spans_by_page: Dict[int, List[Dict[str, Any]]] = {}
    for sp in text_spans:
        spans_by_page.setdefault(sp["page_index"], []).append(sp)

    for page_index in range(doc.page_count):
        page = doc.load_page(page_index)
        annot = page.first_annot
        counter = 0

        while annot:
            try:
                subtype_num, subtype_name = annot.type
                rect = annot.rect
                bbox = [float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)]

                media = {
                    "id": f"media_p{page_index}_{counter}",
                    "page_index": page_index,
                    "bbox": bbox,
                    "source": subtype_name,
                    "annotation_subtype": subtype_name,
                    "filename": None,
                    "media_class": "unknown",
                    "stream_info_method": "unknown",
                    "has_detectable_transcript": False,
                    "has_detectable_captions": False,
                    "has_detectable_audio_description": False,
                    "has_detectable_media_alternative": False,
                    "looks_live": False,
                    "nearby_text_ids": [],
                    "notes": [],
                }

                added = False

                if subtype_num == fitz.PDF_ANNOT_SOUND:
                    try:
                        sound_info = annot.get_sound()
                        if sound_info:
                            media["media_class"] = "audio_only"
                            media["stream_info_method"] = "sound_annotation"
                            media["notes"].append("sound_annotation_detected")
                            added = True
                    except Exception as e:
                        media["notes"].append(f"get_sound_failed:{type(e).__name__}")

                elif subtype_num == fitz.PDF_ANNOT_RICH_MEDIA:
                    try:
                        cont = doc.xref_get_key(annot.xref, "RichMediaContent/Assets/Names")
                        if cont[0] == "array":
                            array = cont[1][1:-1].strip()

                            if array:
                                if array[0] == "(":
                                    i = array.find(")")
                                else:
                                    i = array.find(">")

                                xref_str = array[i + 1:].strip()

                                if xref_str.endswith(" 0 R"):
                                    xref = int(xref_str[:-4].strip())

                                    fname_info = doc.xref_get_key(xref, "F")
                                    ef_info = doc.xref_get_key(xref, "EF/F")

                                    if fname_info[0] != "null":
                                        media["filename"] = fname_info[1].strip("()")

                                    media["media_class"] = classify_by_filename(media["filename"])
                                    media["stream_info_method"] = "filename"

                                    if ef_info[0] != "null":
                                        media_xref = int(ef_info[1].split()[0])
                                        raw = doc.xref_stream_raw(media_xref)

                                        if raw:
                                            suffix = Path(media["filename"] or "media.bin").suffix or ".bin"
                                            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
                                            tmp.write(raw)
                                            tmp.flush()
                                            tmp.close()

                                            try:
                                                probe = run_ffprobe(tmp.name)
                                                if probe.get("ok"):
                                                    media["media_class"] = probe["media_class"]
                                                    media["stream_info_method"] = "ffprobe"
                                                    if probe.get("has_subtitle", False):
                                                        media["has_detectable_captions"] = True
                                                        media["notes"].append("subtitle_stream_detected_by_ffprobe")
                                                else:
                                                    media["notes"].append(probe.get("reason", "ffprobe_failed"))
                                            finally:
                                                try:
                                                    os.unlink(tmp.name)
                                                except Exception:
                                                    pass

                                    added = True
                    except Exception as e:
                        media["notes"].append(f"richmedia_failed:{type(e).__name__}")

                elif subtype_name == "Screen":
                    try:
                        action_info = doc.xref_get_key(annot.xref, "A/F")
                        if action_info[0] != "null":
                            media["filename"] = action_info[1].strip("()")
                            media["media_class"] = classify_by_filename(media["filename"])
                            media["stream_info_method"] = "filename"
                            added = True
                    except Exception as e:
                        media["notes"].append(f"screen_failed:{type(e).__name__}")

                if added:
                    for sp in spans_by_page.get(page_index, []):
                        sb = sp.get("bbox")
                        st = (sp.get("text") or "").strip().lower()
                        if not sb or not st:
                            continue

                        h_gap = max(bbox[0] - sb[2], sb[0] - bbox[2], 0.0)
                        v_gap = max(bbox[1] - sb[3], sb[1] - bbox[3], 0.0)

                        if h_gap <= 220.0 and v_gap <= 80.0:
                            if sp.get("id"):
                                media["nearby_text_ids"].append(sp["id"])
                            if TRANSCRIPT_POSITIVE_RE.search(st) and not TRANSCRIPT_NEGATIVE_RE.search(st):
                                media["has_detectable_transcript"] = True
                                media["has_detectable_media_alternative"] = True

                            if CAPTION_POSITIVE_RE.search(st) and not CAPTION_NEGATIVE_RE.search(st):
                                media["has_detectable_captions"] = True
                                media["has_detectable_media_alternative"] = True

                            if AUDIO_DESC_POSITIVE_RE.search(st) and not AUDIO_DESC_NEGATIVE_RE.search(st):
                                media["has_detectable_audio_description"] = True
                                media["has_detectable_media_alternative"] = True

                            if MEDIA_ALT_POSITIVE_RE.search(st) and not MEDIA_ALT_NEGATIVE_RE.search(st):
                                media["has_detectable_media_alternative"] = True

                            if LIVE_RE.search(st):
                                media["looks_live"] = True

                    media_occurrences.append(media)
                    counter += 1

            except Exception:
                pass

            annot = annot.next

    return media_occurrences


def extract_media_annotations_pikepdf(pdf_path: str, pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Detect likely prerecorded media in PDFs using annotation dictionaries.
    Targets:
      - /RichMedia
      - /Screen
      - annotations/actions that reference media-like assets
    """
    media_occurrences: List[Dict[str, Any]] = []
    page_heights = {p["page_index"]: float(p["height"]) for p in pages}

    try:
        with pikepdf.open(pdf_path) as pdf:
            for page_index, page in enumerate(pdf.pages):
                annots = page.get("/Annots", None)
                if not annots:
                    continue

                for idx, annot in enumerate(annots):
                    try:
                        obj = annot
                        subtype = safe_pdf_name(obj.get("/Subtype", None))
                        rect = obj.get("/Rect", None)

                        bbox = None
                        if rect and len(rect) == 4:
                            x0, y0, x1, y1 = [float(v) for v in rect]
                            bbox = pdfminer_bbox_to_pymupdf_bbox(
                                [x0, y0, x1, y1],
                                page_heights.get(page_index, 0.0)
                            )

                        media_info = {
                            "id": f"media_p{page_index}_{idx}",
                            "page_index": page_index,
                            "bbox": bbox,
                            "source": subtype or "unknown",
                            "kind": "unknown",
                            "filename": None,
                            "mime_type": None,
                            "has_detectable_media_alternative": False,
                            "nearby_text_ids": [],
                            "notes": [],
                        }

                        is_media_like = False

                        if subtype == "RichMedia":
                            is_media_like = True
                            media_info["source"] = "RichMedia"

                            rich = obj.get("/RichMediaContent", None)
                            if rich:
                                assets = rich.get("/Assets", None)
                                if assets:
                                    names = assets.get("/Names", None)
                                    if names:
                                        for i in range(0, len(names), 2):
                                            try:
                                                fname = str(names[i])
                                                media_info["filename"] = fname
                                                media_info["kind"] = _classify_media_kind(fname, None)
                                                break
                                            except Exception:
                                                pass

                        elif subtype == "Screen":
                            is_media_like = True
                            media_info["source"] = "Screen"

                            a = obj.get("/A", None)
                            if a:
                                fobj = a.get("/F", None)
                                if fobj is not None:
                                    try:
                                        fname = str(fobj)
                                        media_info["filename"] = fname
                                        media_info["kind"] = _classify_media_kind(fname, None)
                                    except Exception:
                                        pass

                        elif subtype == "FileAttachment":
                            fs = obj.get("/FS", None)
                            if fs:
                                try:
                                    fname = str(fs.get("/F", "")) if isinstance(fs, pikepdf.Dictionary) else str(fs)
                                    kind = _classify_media_kind(fname, None)
                                    if kind in {"audio", "video"}:
                                        is_media_like = True
                                        media_info["source"] = "FileAttachment"
                                        media_info["filename"] = fname
                                        media_info["kind"] = kind
                                except Exception:
                                    pass

                        if is_media_like:
                            media_occurrences.append(media_info)

                    except Exception:
                        continue

    except Exception:
        pass

    return media_occurrences


def extract_embedded_files_pikepdf(pdf_path: str) -> List[Dict[str, Any]]:
    """
    Extract document-level embedded files from /Names -> /EmbeddedFiles.
    This catches media that is embedded in the PDF but not attached
    as a visible page annotation.
    """
    media_occurrences: List[Dict[str, Any]] = []

    try:
        with pikepdf.open(pdf_path) as pdf:
            root = pdf.Root
            names = root.get("/Names", None)
            if not names:
                return media_occurrences

            embedded_files = names.get("/EmbeddedFiles", None)
            if not embedded_files:
                return media_occurrences

            names_array = embedded_files.get("/Names", None)
            if not names_array:
                return media_occurrences

            counter = 0

            for i in range(0, len(names_array), 2):
                try:
                    name_obj = names_array[i]
                    spec = names_array[i + 1]

                    filename = str(name_obj) if name_obj is not None else None
                    description = None
                    has_subtitle_stream = False
                    media_class = "unknown"
                    notes = ["document_level_embedded_file"]

                    if isinstance(spec, pikepdf.Dictionary):
                        try:
                            if spec.get("/Desc", None) is not None:
                                description = str(spec.get("/Desc"))
                        except Exception:
                            pass

                        try:
                            ef = spec.get("/EF", None)
                            if ef and isinstance(ef, pikepdf.Dictionary):
                                embedded_stream = ef.get("/F", None)
                                if embedded_stream is not None:
                                    suffix = Path(filename or "embedded.bin").suffix or ".bin"
                                    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
                                    try:
                                        raw_bytes = bytes(embedded_stream.read_bytes())
                                        tmp.write(raw_bytes)
                                        tmp.flush()
                                        tmp.close()

                                        probe = run_ffprobe(tmp.name)
                                        if probe.get("ok"):
                                            media_class = probe.get("media_class", "unknown")
                                            notes.append("ffprobe_embedded_file_checked")
                                            has_subtitle_stream = probe.get("has_subtitle", False)
                                        else:
                                            notes.append(probe.get("reason", "ffprobe_failed"))
                                            media_class = classify_by_filename(filename)
                                    finally:
                                        try:
                                            os.unlink(tmp.name)
                                        except Exception:
                                            pass
                                else:
                                    media_class = classify_by_filename(filename)
                        except Exception:
                            media_class = classify_by_filename(filename)
                    else:
                        media_class = classify_by_filename(filename)

                    media_occurrences.append({
                        "id": f"embedded_media_{counter}",
                        "page_index": None,
                        "bbox": None,
                        "source": "EmbeddedFiles",
                        "annotation_subtype": None,
                        "filename": filename,
                        "media_class": media_class,
                        "stream_info_method": "embedded_files_name_tree",
                        "has_detectable_transcript": False,
                        "has_detectable_captions": has_subtitle_stream,
                        "has_detectable_audio_description": False,
                        "has_detectable_media_alternative": False,
                        "looks_live": False,
                        "nearby_text_ids": [],
                        "notes": notes + ([f"description:{description}"] if description else []),
                    })
                    counter += 1

                except Exception:
                    continue

    except Exception:
        pass

    return media_occurrences


def annotate_media_alternatives(
    media_occurrences: List[Dict[str, Any]],
    text_spans: List[Dict[str, Any]],
    max_h_gap: float = 220.0,
    max_v_gap: float = 80.0,
):
    """
    Add transcript / captions / audio-description / media-alternative evidence
    to each detected media item.

    - For page-located media: search nearby text on the same page.
    - For document-level embedded media: search all document text.
    """
    spans_by_page: Dict[int, List[Dict[str, Any]]] = {}
    for sp in text_spans:
        spans_by_page.setdefault(sp["page_index"], []).append(sp)

    for media in media_occurrences:
        page_index = media.get("page_index")
        mb = media.get("bbox")

        nearby_ids: List[str] = []
        found_alt = False
        candidate_spans: List[Dict[str, Any]] = []

        if page_index is not None and mb:
            for sp in spans_by_page.get(page_index, []):
                sb = sp.get("bbox")
                st = (sp.get("text") or "").strip().lower()
                if not sb or not st:
                    continue

                h_gap = _horizontal_gap(mb, sb)
                v_gap = _vertical_gap(mb, sb)

                if h_gap <= max_h_gap and v_gap <= max_v_gap:
                    candidate_spans.append(sp)
                    if sp.get("id"):
                        nearby_ids.append(sp["id"])

        else:
            for sp in text_spans:
                st = (sp.get("text") or "").strip().lower()
                if not st:
                    continue
                candidate_spans.append(sp)
                if sp.get("id"):
                    nearby_ids.append(sp["id"])

        for sp in candidate_spans:
            st = (sp.get("text") or "").strip().lower()

            if any(keyword in st for keyword in ALT_TEXT_KEYWORDS):
                found_alt = True

            if TRANSCRIPT_POSITIVE_RE.search(st) and not TRANSCRIPT_NEGATIVE_RE.search(st):
                media["has_detectable_transcript"] = True
                media["has_detectable_media_alternative"] = True

            if CAPTION_POSITIVE_RE.search(st) and not CAPTION_NEGATIVE_RE.search(st):
                media["has_detectable_captions"] = True
                media["has_detectable_media_alternative"] = True

            if AUDIO_DESC_POSITIVE_RE.search(st) and not AUDIO_DESC_NEGATIVE_RE.search(st):
                media["has_detectable_audio_description"] = True
                media["has_detectable_media_alternative"] = True

            if any(k in st for k in LIVE_KEYWORDS):
                media["looks_live"] = True

        media["nearby_text_ids"] = nearby_ids
        media["has_detectable_media_alternative"] = (
    media.get("has_detectable_media_alternative", False) or found_alt
)

def default_presentation_semantics() -> Dict[str, Any]:
    return {
        "is_logo_text": False,
        "logo_confidence": 0.0,
        "logo_reason": None,

        "is_decorative_text": False,
        "decorative_confidence": 0.0,
        "decorative_reason": None,

        "is_text_in_image_context": False,
        "image_context_confidence": 0.0,
        "image_context_reason": None,
        "overlapping_image_ids": [],

        "is_ui_label": False,
        "ui_component_type": None,
        "ui_state": None
    }

def default_resize_risk() -> Dict[str, Any]:
    return {
        "font_size_pt": 0.0,
        "is_small_text": False,
        "span_width": 0.0,
        "span_height": 0.0,
        "estimated_scale_200_bbox": None,
        "has_nearby_text": False,
        "nearby_text_ids": [],
        "same_line_overlap_ids": [],
        "paragraph_flow_neighbor_ids": [],
        "has_nearby_graphic": False,
        "nearby_graphic_ids": [],
        "has_nearby_widget": False,
        "nearby_widget_ids": [],
        "clipping_container_ids": [],
        "would_overlap_on_scale_200": False,
        "suppressed_due_to_semantics": False,
        "risk_score": 0
    }


def default_non_text_contrast() -> Dict[str, Any]:
    return {
        "adjacent_rgb": None,
        "method": None,
        "contrast_against_stroke": None,
        "contrast_against_fill": None,
        "contrast_against_border": None,
        "passes_3_1": None
    }


def annotate_ui_labels(
    text_spans: List[Dict[str, Any]],
    widgets: List[Dict[str, Any]],
    near_margin: float = 12.0
):
    widgets_by_page: Dict[int, List[Dict[str, Any]]] = {}
    for w in widgets:
        widgets_by_page.setdefault(w["page_index"], []).append(w)

    for span in text_spans:
        sem = span.setdefault("presentation_semantics", default_presentation_semantics())
        bbox = span.get("bbox")
        page_index = span.get("page_index")

        if not bbox:
            continue

        for widget in widgets_by_page.get(page_index, []):
            widget_bbox = widget.get("bbox")
            if not widget_bbox:
                continue

            if bbox_intersects(bbox, widget_bbox, margin=near_margin):
                sem["is_ui_label"] = True
                sem["ui_component_type"] = widget.get("field_type")
                sem["ui_state"] = widget.get("ui_state")
                break


def annotate_text_in_image_context(
    text_spans: List[Dict[str, Any]],
    image_occurrences: List[Dict[str, Any]],
    overlap_threshold: float = 0.15,
    near_margin: float = 3.0
):
    images_by_page: Dict[int, List[Dict[str, Any]]] = {}
    for img in image_occurrences:
        images_by_page.setdefault(img["page_index"], []).append(img)

    for span in text_spans:
        sem = span.setdefault("presentation_semantics", default_presentation_semantics())
        span_bbox = span.get("bbox")
        page_index = span.get("page_index")

        if not span_bbox:
            continue

        overlapping_ids = []
        best_ratio = 0.0

        for img in images_by_page.get(page_index, []):
            img_bbox = img.get("bbox")
            if not img_bbox:
                continue

            ratio = intersection_ratio_of_span(span_bbox, img_bbox)
            near = bbox_intersects(span_bbox, img_bbox, margin=near_margin)

            if ratio >= overlap_threshold or (ratio > 0 and near):
                overlapping_ids.append(img["id"])
                best_ratio = max(best_ratio, ratio)

        if overlapping_ids:
            sem["is_text_in_image_context"] = True
            sem["image_context_confidence"] = float(round(max(best_ratio, 0.7), 3))
            sem["image_context_reason"] = "Text span overlaps or is embedded in an image region."
            sem["overlapping_image_ids"] = overlapping_ids


def annotate_logo_like_text(
    text_spans: List[Dict[str, Any]],
    pages: List[Dict[str, Any]]
):
    page_heights = {p["page_index"]: float(p["height"]) for p in pages}

    for span in text_spans:
        sem = span.setdefault("presentation_semantics", default_presentation_semantics())
        text = (span.get("text") or "").strip()
        bbox = span.get("bbox")
        font_size = float(span.get("font", {}).get("size", 0.0))
        page_index = span.get("page_index")

        if not text or not bbox:
            continue

        page_height = page_heights.get(page_index, 0.0)
        if page_height <= 0:
            continue

        y0 = bbox[1]
        in_top_band = y0 <= page_height * 0.20
        short_text = len(text) <= 25
        one_or_two_words = len(text.split()) <= 2
        largeish = font_size >= 14.0
        uppercase_like = text.isupper() and any(ch.isalpha() for ch in text)

        score = 0.0
        reasons = []

        if in_top_band:
            score += 0.35
            reasons.append("top_of_page")
        if short_text and one_or_two_words:
            score += 0.25
            reasons.append("short_brand_like_text")
        if largeish:
            score += 0.20
            reasons.append("large_font")
        if uppercase_like:
            score += 0.20
            reasons.append("uppercase_style")

        if score >= 0.75:
            sem["is_logo_text"] = True
            sem["logo_confidence"] = float(round(score, 3))
            sem["logo_reason"] = ", ".join(reasons)


def annotate_decorative_text(text_spans: List[Dict[str, Any]]):
    text_counts: Dict[str, int] = {}
    for span in text_spans:
        norm = _normalize_repeat_text(span.get("text", ""))
        if norm:
            text_counts[norm] = text_counts.get(norm, 0) + 1

    for span in text_spans:
        sem = span.setdefault("presentation_semantics", default_presentation_semantics())
        text = (span.get("text") or "").strip()
        bbox = span.get("bbox")
        font_size = float(span.get("font", {}).get("size", 0.0))
        norm = _normalize_repeat_text(text)

        if not text or not bbox:
            continue

        repeated = text_counts.get(norm, 0) >= 3
        short_text = len(text) <= 12
        huge_text = font_size >= 28.0
        uppercase_like = text.isupper() and any(ch.isalpha() for ch in text)

        score = 0.0
        reasons = []

        if repeated:
            score += 0.35
            reasons.append("repeated_text")
        if short_text:
            score += 0.15
            reasons.append("short_text")
        if huge_text:
            score += 0.30
            reasons.append("very_large_text")
        if uppercase_like:
            score += 0.20
            reasons.append("uppercase_style")

        if score >= 0.80:
            sem["is_decorative_text"] = True
            sem["decorative_confidence"] = float(round(score, 3))
            sem["decorative_reason"] = ", ".join(reasons)



EXPLICIT_COLOR_ONLY_PATTERNS = [
    re.compile(
        r"\b(required|mandatory)\s+fields?\s+(are|is)\s+(marked|shown|indicated|highlighted|displayed)\s+(in|with|by|using)\s+(red|green|blue|yellow|orange|purple|pink|grey|gray)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(red|green|blue|yellow|orange|purple|pink|grey|gray)\s+(means?|indicates?|shows?|represents?|denotes?|signals?)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bcolor[- ]coded\b", re.IGNORECASE),
    re.compile(
        r"\b(items?|fields?|rows?|cells?|entries|text)\s+(in|shown in|marked in|highlighted in)\s+(red|green|blue|yellow|orange|purple|pink|grey|gray)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(click|select|choose)\s+the\s+(red|green|blue|yellow|orange|purple|pink|grey|gray)\s+(button|link|tab|option|item)\b",
        re.IGNORECASE,
    ),
]

_REQUIRED_CUE_PATTERN = re.compile(r"(^\*$)|(\*)|\brequired\b|\bmandatory\b", re.IGNORECASE)

NEUTRAL_REPEATABLE_LABELS = {
    "status", "label", "item", "type", "level", "flag", "category", "state"
}

MARKER_TEXTS = {
    "●", "•", "○", "◦", "▪", "■", "□", "▲", "△", "▼", "▽", "◆", "◇", "★", "☆"
}

EXCLUDED_SEMANTIC_LABELS = {
    "active", "inactive", "enabled", "disabled",
    "open", "closed", "approved", "rejected",
    "pass", "fail", "passed", "failed",
    "yes", "no", "true", "false",
    "success", "error", "warning",
    "valid", "invalid"
}


def _is_redish(rgb: list[int]) -> bool:
    if not rgb or len(rgb) < 3:
        return False
    r, g, b = rgb[:3]
    return r >= 150 and r >= g + 40 and r >= b + 40


def _collect_near_widget_label_spans(
    widget: dict,
    page_spans: list[dict],
    max_h_gap: float = 120.0,
    max_v_gap: float = 30.0,
) -> list[dict]:
    widget_bbox = widget.get("bbox")
    if not widget_bbox:
        return []

    out = []
    for sp in page_spans:
        sb = sp.get("bbox")
        st = (sp.get("text") or "").strip()
        if not sb or not st:
            continue

        h_gap = _horizontal_gap(widget_bbox, sb)
        v_gap = _vertical_gap(widget_bbox, sb)

        if h_gap <= max_h_gap and v_gap <= max_v_gap:
            out.append(sp)

    return out


def _collect_nearby_text(
    target_span: dict,
    page_spans: list[dict],
    max_h_gap: float = 90.0,
    max_v_gap: float = 20.0,
) -> list[dict]:
    tb = target_span.get("bbox")
    if not tb:
        return []

    out = []
    for sp in page_spans:
        if sp.get("id") == target_span.get("id"):
            continue

        sb = sp.get("bbox")
        st = (sp.get("text") or "").strip()
        if not sb or not st:
            continue

        h_gap = _horizontal_gap(tb, sb)
        v_gap = _vertical_gap(tb, sb)

        if h_gap <= max_h_gap and v_gap <= max_v_gap:
            out.append(sp)

    return out


def _is_marker_or_identical_label_candidate(text: str) -> bool:
    t = (text or "").strip()
    nt = _normalize_repeat_text(t)

    if not t:
        return False

    if t in MARKER_TEXTS:
        return True

    if nt in NEUTRAL_REPEATABLE_LABELS:
        return True

    if len(nt.split()) == 1 and len(nt) <= 8 and nt not in EXCLUDED_SEMANTIC_LABELS:
        return True

    return False


def _same_pattern_axis(
    b1: list[float],
    b2: list[float],
    x_tol: float = 24.0,
    y_tol: float = 16.0,
) -> bool:
    c1x = (b1[0] + b1[2]) / 2.0
    c1y = (b1[1] + b1[3]) / 2.0
    c2x = (b2[0] + b2[2]) / 2.0
    c2y = (b2[1] + b2[3]) / 2.0

    same_col = abs(c1x - c2x) <= x_tol
    same_row = abs(c1y - c2y) <= y_tol
    return same_col or same_row


def detect_link_color_only(document: dict) -> list[dict]:
    issues: list[dict] = []
    doc = document.get("document", document)

    text_spans = doc.get("text_spans", [])
    links = doc.get("links", [])
    graphics = doc.get("graphics", [])

    links_by_page: dict[int, list[dict]] = {}
    graphics_by_page: dict[int, list[dict]] = {}
    spans_by_page: dict[int, list[dict]] = {}

    for link in links:
        p = link.get("page_index")
        if p is not None:
            links_by_page.setdefault(p, []).append(link)

    for g in graphics:
        p = g.get("page_index")
        if p is not None:
            graphics_by_page.setdefault(p, []).append(g)

    for sp in text_spans:
        p = sp.get("page_index")
        if p is not None:
            spans_by_page.setdefault(p, []).append(sp)

    reported_link_ids = set()

    for page_index, page_links in links_by_page.items():
        page_spans = spans_by_page.get(page_index, [])
        page_graphics = graphics_by_page.get(page_index, [])

        for link in page_links:
            link_id = link.get("id")
            link_bbox = link.get("bbox")

            if not link_id or not link_bbox or link_id in reported_link_ids:
                continue

            linked_spans = [
                sp for sp in page_spans
                if sp.get("bbox")
                and (sp.get("text") or "").strip()
                and bbox_intersects(sp["bbox"], link_bbox, margin=2.0)
            ]

            if not linked_spans:
                issues.append(
                    make_issue(
                        criterion="1.4.1",
                        issue="link_color_only_needs_review",
                        location={
                            "page": page_index,
                            "link_id": link_id,
                        },
                        severity="needs_review",
                        recommendation="Linked text could not be determined automatically."
                    )
                )
                reported_link_ids.add(link_id)
                continue

            linked_spans = _sort_spans_reading_order(linked_spans)

            combined_text = " ".join(
                (sp.get("text") or "").strip()
                for sp in linked_spans
                if (sp.get("text") or "").strip()
            ).strip()

            if not combined_text:
                issues.append(
                    make_issue(
                        criterion="1.4.1",
                        issue="link_color_only_needs_review",
                        location={
                            "page": page_index,
                            "link_id": link_id,
                            "span_ids": [sp.get("id") for sp in linked_spans if sp.get("id")],
                        },
                        severity="needs_review",
                        recommendation="Linked label text could not be interpreted automatically."
                    )
                )
                reported_link_ids.add(link_id)
                continue

            if is_url_like_text(combined_text):
                issues.append(
                    make_issue(
                        criterion="1.4.1",
                        issue="link_non_color_cue_detected",
                        location={
                            "page": page_index,
                            "link_id": link_id,
                            "span_ids": [sp.get("id") for sp in linked_spans if sp.get("id")],
                        },
                        severity="pass",
                        recommendation="Link text itself provides a visible cue."
                    )
                )
                reported_link_ids.add(link_id)
                continue

            combined_bbox = _union_bboxes([sp["bbox"] for sp in linked_spans if sp.get("bbox")])
            if not combined_bbox:
                issues.append(
                    make_issue(
                        criterion="1.4.1",
                        issue="link_color_only_needs_review",
                        location={
                            "page": page_index,
                            "link_id": link_id,
                            "span_ids": [sp.get("id") for sp in linked_spans if sp.get("id")],
                        },
                        severity="needs_review",
                        recommendation="Link region could not be computed automatically."
                    )
                )
                reported_link_ids.add(link_id)
                continue

            if has_underline_graphic(combined_bbox, page_graphics):
                issues.append(
                    make_issue(
                        criterion="1.4.1",
                        issue="link_non_color_cue_detected",
                        location={
                            "page": page_index,
                            "link_id": link_id,
                            "span_ids": [sp.get("id") for sp in linked_spans if sp.get("id")],
                        },
                        severity="pass",
                        recommendation="A non-color cue was detected."
                    )
                )
                reported_link_ids.add(link_id)
                continue

            if has_enclosing_box_cue(combined_bbox, page_graphics):
                issues.append(
                    make_issue(
                        criterion="1.4.1",
                        issue="link_non_color_cue_detected",
                        location={
                            "page": page_index,
                            "link_id": link_id,
                            "span_ids": [sp.get("id") for sp in linked_spans if sp.get("id")],
                        },
                        severity="pass",
                        recommendation="A non-color cue was detected."
                    )
                )
                reported_link_ids.add(link_id)
                continue

            rep_span = linked_spans[0]
            rep_font = rep_span.get("font", {})
            rep_color = rep_span.get("color", {}).get("fill_rgb")

            if not rep_color:
                issues.append(
                    make_issue(
                        criterion="1.4.1",
                        issue="link_color_only_needs_review",
                        location={
                            "page": page_index,
                            "link_id": link_id,
                            "span_ids": [sp.get("id") for sp in linked_spans if sp.get("id")],
                        },
                        severity="needs_review",
                        recommendation="Link color could not be determined automatically."
                    )
                )
                reported_link_ids.add(link_id)
                continue

            neighbor_map: dict[str, dict] = {}

            for lsp in linked_spans:
                neighbors = collect_same_line_non_link_neighbors(
                    target_span=lsp,
                    text_spans=page_spans,
                    page_links=[link],
                    max_horizontal_distance=220.0,
                )
                for n in neighbors:
                    nid = n.get("id")
                    if nid:
                        neighbor_map[nid] = n

            if not neighbor_map:
                issues.append(
                    make_issue(
                        criterion="1.4.1",
                        issue="link_color_only_needs_review",
                        location={
                            "page": page_index,
                            "link_id": link_id,
                            "span_ids": [sp.get("id") for sp in linked_spans if sp.get("id")],
                        },
                        severity="needs_review",
                        recommendation="Comparable surrounding text was not found automatically."
                    )
                )
                reported_link_ids.add(link_id)
                continue

            typography_neighbors = []
            for n in neighbor_map.values():
                n_text = (n.get("text") or "").strip()
                n_color = n.get("color", {}).get("fill_rgb")
                n_font = n.get("font", {})

                if not n_text or not n_color:
                    continue

                if similar_font_properties(rep_font, n_font, size_tol=1.0):
                    typography_neighbors.append(n)

            if not typography_neighbors:
                issues.append(
                    make_issue(
                        criterion="1.4.1",
                        issue="link_color_only_passed",
                        location={
                            "page": page_index,
                            "link_id": link_id,
                            "span_ids": [sp.get("id") for sp in linked_spans if sp.get("id")],
                        },
                        severity="pass",
                        recommendation="No color-only failure was detected."
                    )
                )
                reported_link_ids.add(link_id)
                continue

            distinct_neighbors = []
            for n in typography_neighbors:
                n_color = n.get("color", {}).get("fill_rgb")
                if colors_are_distinct(rep_color, n_color):
                    distinct_neighbors.append(n)

            if not distinct_neighbors:
                issues.append(
                    make_issue(
                        criterion="1.4.1",
                        issue="link_color_only_passed",
                        location={
                            "page": page_index,
                            "link_id": link_id,
                            "span_ids": [sp.get("id") for sp in linked_spans if sp.get("id")],
                        },
                        severity="pass",
                        recommendation="No color-only failure was detected."
                    )
                )
                reported_link_ids.add(link_id)
                continue

            issues.append(
                make_issue(
                    criterion="1.4.1",
                    issue="link_distinguished_by_color_only",
                    location={
                        "page": page_index,
                        "link_id": link_id,
                        "span_ids": [sp.get("id") for sp in linked_spans if sp.get("id")],
                    },
                    severity="high",
                    recommendation="Add a non-color cue such as underlining.",
                )
            )
            reported_link_ids.add(link_id)

    return issues


def detect_explicit_color_only_instructions(document: dict) -> list[dict]:
    issues: list[dict] = []
    doc = document.get("document", document)

    text_blocks = doc.get("text_blocks", [])
    text_spans = doc.get("text_spans", [])

    reported_keys = set()
    found_any = False

    for block in text_blocks:
        block_id = block.get("id")
        text = (block.get("text") or "").strip()
        page_index = block.get("page_index")

        if not text:
            continue

        for pattern in EXPLICIT_COLOR_ONLY_PATTERNS:
            if pattern.search(text):
                key = ("block", block_id)
                if key in reported_keys:
                    break

                issues.append(
                    make_issue(
                        criterion="1.4.1",
                        issue="explicit_color_only_instruction",
                        location={
                            "page": page_index,
                            "block_id": block_id,
                        },
                        severity="high",
                        recommendation="Do not rely on color alone; add another cue.",
                    )
                )
                reported_keys.add(key)
                found_any = True
                break

    for span in text_spans:
        span_id = span.get("id")
        text = (span.get("text") or "").strip()
        page_index = span.get("page_index")

        if not span_id or not text:
            continue

        for pattern in EXPLICIT_COLOR_ONLY_PATTERNS:
            if pattern.search(text):
                key = ("span", span_id)
                if key in reported_keys:
                    break

                issues.append(
                    make_issue(
                        criterion="1.4.1",
                        issue="explicit_color_only_instruction",
                        location={
                            "page": page_index,
                            "span_id": span_id,
                        },
                        severity="high",
                        recommendation="Do not rely on color alone; add another cue.",
                    )
                )
                reported_keys.add(key)
                found_any = True
                break

    if not found_any:
        issues.append(
            make_issue(
                criterion="1.4.1",
                issue="explicit_color_only_instruction_not_detected",
                location={},
                severity="pass",
                recommendation="No explicit color-only instruction was detected."
            )
        )

    return issues


def detect_required_field_color_only(document: dict) -> list[dict]:
    issues: list[dict] = []
    doc = document.get("document", document)

    text_spans = doc.get("text_spans", [])
    widgets = doc.get("widgets", [])

    spans_by_page: dict[int, list[dict]] = {}
    widgets_by_page: dict[int, list[dict]] = {}

    for sp in text_spans:
        p = sp.get("page_index")
        if p is not None:
            spans_by_page.setdefault(p, []).append(sp)

    for w in widgets:
        p = w.get("page_index")
        if p is not None:
            widgets_by_page.setdefault(p, []).append(w)

    reported_span_ids = set()
    found_any = False

    for page_index, page_widgets in widgets_by_page.items():
        page_spans = spans_by_page.get(page_index, [])

        for widget in page_widgets:
            candidate_labels = _collect_near_widget_label_spans(widget, page_spans)

            if not candidate_labels:
                continue

            for sp in candidate_labels:
                span_id = sp.get("id")
                if not span_id or span_id in reported_span_ids:
                    continue

                sp_text = (sp.get("text") or "").strip()
                sp_bbox = sp.get("bbox")
                sp_color = sp.get("color", {}).get("fill_rgb")
                sp_font = sp.get("font", {})

                if not sp_text or not sp_bbox or not sp_color:
                    continue

                if len(sp_text.split()) > 6:
                    continue

                if not _is_redish(sp_color):
                    continue

                if _REQUIRED_CUE_PATTERN.search(sp_text):
                    continue

                nearby_text_spans = _collect_nearby_text(sp, page_spans, max_h_gap=90.0, max_v_gap=20.0)
                nearby_text = " ".join((n.get("text") or "").strip() for n in nearby_text_spans)

                if _REQUIRED_CUE_PATTERN.search(nearby_text):
                    continue

                typography_similar_neighbors = []
                for n in nearby_text_spans:
                    n_text = (n.get("text") or "").strip()
                    n_color = n.get("color", {}).get("fill_rgb")
                    n_font = n.get("font", {})

                    if not n_text or not n_color:
                        continue

                    if similar_font_properties(sp_font, n_font, size_tol=1.0):
                        typography_similar_neighbors.append(n)

                distinct_neighbors = []
                for n in typography_similar_neighbors:
                    n_color = n.get("color", {}).get("fill_rgb")
                    if colors_are_distinct(sp_color, n_color):
                        distinct_neighbors.append(n)

                if not distinct_neighbors:
                    continue

                issues.append(
                    make_issue(
                        criterion="1.4.1",
                        issue="required_field_indicated_by_color_only",
                        location={
                            "page": page_index,
                            "span_id": span_id,
                            "widget_id": widget.get("id"),
                        },
                        severity="high",
                        recommendation="Add a cue such as '*' or 'required'.",
                    )
                )
                reported_span_ids.add(span_id)
                found_any = True

    if not found_any:
        issues.append(
            make_issue(
                criterion="1.4.1",
                issue="required_field_color_only_not_detected",
                location={},
                severity="pass",
                recommendation="No action needed since no required field marked only by color was detected."
            )
        )

    return issues


def detect_repeated_identical_marker_or_label_color_only(document: dict) -> list[dict]:
    """
    Detect repeated identical markers/labels whose distinction appears
    to rely mainly on color.
    """
    issues: list[dict] = []
    doc = document.get("document", document)

    text_spans = doc.get("text_spans", [])
    links = doc.get("links", [])
    graphics = doc.get("graphics", [])

    spans_by_page: dict[int, list[dict]] = {}
    links_by_page: dict[int, list[dict]] = {}
    graphics_by_page: dict[int, list[dict]] = {}

    for sp in text_spans:
        p = sp.get("page_index")
        if p is not None:
            spans_by_page.setdefault(p, []).append(sp)

    for lk in links:
        p = lk.get("page_index")
        if p is not None:
            links_by_page.setdefault(p, []).append(lk)

    for g in graphics:
        p = g.get("page_index")
        if p is not None:
            graphics_by_page.setdefault(p, []).append(g)

    reported_group_keys = set()
    found_any = False

    for page_index, page_spans in spans_by_page.items():
        page_links = links_by_page.get(page_index, [])
        page_graphics = graphics_by_page.get(page_index, [])

        groups: dict[str, list[dict]] = {}

        for sp in page_spans:
            text = (sp.get("text") or "").strip()
            bbox = sp.get("bbox")
            color = sp.get("color", {}).get("fill_rgb")
            sem = sp.get("presentation_semantics", {})

            if not text or not bbox or not color:
                continue

            if sem.get("is_logo_text", False):
                continue
            if sem.get("is_decorative_text", False):
                continue
            if sem.get("is_text_in_image_context", False):
                continue

            if any(
                lk.get("bbox") and bbox_intersects(bbox, lk["bbox"], margin=2.0)
                for lk in page_links
            ):
                continue

            if has_enclosing_box_cue(bbox, page_graphics):
                continue

            if not _is_marker_or_identical_label_candidate(text):
                continue

            nt = _normalize_repeat_text(text)
            group_key = text if text in MARKER_TEXTS else nt
            groups.setdefault(group_key, []).append(sp)

        for group_key, group_spans in groups.items():
            is_marker_group = group_key in MARKER_TEXTS
            min_group_size = 2 if is_marker_group else 3

            if len(group_spans) < min_group_size:
                continue

            base_font = group_spans[0].get("font", {})
            consistent = [
                sp for sp in group_spans
                if similar_font_properties(base_font, sp.get("font", {}), size_tol=1.0)
            ]

            if len(consistent) < min_group_size:
                continue

            patterned = []
            for sp in consistent:
                sb = sp.get("bbox")
                if not sb:
                    continue

                aligned_peers = 0
                for other in consistent:
                    if other.get("id") == sp.get("id"):
                        continue
                    ob = other.get("bbox")
                    if not ob:
                        continue
                    if _same_pattern_axis(sb, ob):
                        aligned_peers += 1

                if aligned_peers >= 1:
                    patterned.append(sp)

            if len(patterned) < min_group_size:
                continue

            color_buckets: dict[tuple[int, int, int], list[dict]] = {}
            for sp in patterned:
                c = sp.get("color", {}).get("fill_rgb")
                if not c or len(c) < 3:
                    continue
                key = (int(c[0]), int(c[1]), int(c[2]))
                color_buckets.setdefault(key, []).append(sp)

            if len(color_buckets) < 2:
                continue

            distinct_color_keys = list(color_buckets.keys())
            has_clear_difference = False
            for i in range(len(distinct_color_keys)):
                for j in range(i + 1, len(distinct_color_keys)):
                    if colors_are_distinct(
                        list(distinct_color_keys[i]),
                        list(distinct_color_keys[j]),
                    ):
                        has_clear_difference = True
                        break
                if has_clear_difference:
                    break

            if not has_clear_difference:
                continue

            report_key = (page_index, group_key)
            if report_key in reported_group_keys:
                continue

            issues.append(
                make_issue(
                    criterion="1.4.1",
                    issue="repeated_identical_marker_or_label_distinguished_by_color_only",
                    location={
                        "page": page_index,
                        "group_text": group_key,
                        "span_ids": [sp.get("id") for sp in patterned if sp.get("id")],
                    },
                    severity="medium",
                    recommendation="Add a non-color cue to distinguish these items.",
                )
            )
            reported_group_keys.add(report_key)
            found_any = True

    if not found_any:
        issues.append(
            make_issue(
                criterion="1.4.1",
                issue="repeated_identical_marker_or_label_color_only_not_detected",
                location={},
                severity="pass",
                recommendation="No repeated identical items distinguished only by color were detected."
            )
        )

    return issues


def normalize_label(text: Optional[str]) -> str:
    if not text:
        return ""
    text = text.strip().lower()
    text = re.sub(r"[_\-]+", " ", text)      
    text = re.sub(r"[^\w\s]", "", text)      
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def matching_widget_for_acrofield(
    field: Dict[str, Any],
    widgets: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    field_name = (field.get("name") or "").strip().lower()
    field_page = field.get("page_index")

    if not field_name:
        return None

    if field_page is not None:
        for w in widgets:
            widget_name = (w.get("field_name") or "").strip().lower()
            if w.get("page_index") == field_page and widget_name == field_name:
                return w

    matches = []
    for w in widgets:
        widget_name = (w.get("field_name") or "").strip().lower()
        if widget_name == field_name:
            matches.append(w)

    if len(matches) == 1:
        return matches[0]

    return None


def collect_label(widget: Dict[str, Any], page_spans: List[Dict[str, Any]], max_h_gap: float = 140.0, max_v_gap: float = 35.0,
) -> List[Dict[str, Any]]:
    widget_bbox = widget.get("bbox")
    if not widget_bbox:
        return []

    candidates = []
    _, _, wx1, wy1 = widget_bbox
    wcx, wcy = center_of_bbox(widget_bbox)

    for sp in page_spans:
        sb = sp.get("bbox")
        st = (sp.get("text") or "").strip()
        if not sb or not st:
            continue

        h_gap = _horizontal_gap(widget_bbox, sb)
        v_gap = _vertical_gap(widget_bbox, sb)

        if h_gap <= max_h_gap and v_gap <= max_v_gap:
            _, _, sx1, sy1 = sb
            scx, scy = center_of_bbox(sb)

            is_left = sx1 <= wx1
            is_above = sy1 <= wy1

            candidates.append((
                0 if (is_left or is_above) else 1,
                v_gap,
                h_gap,
                abs(scy - wcy),
                abs(scx - wcx),
                sp
            ))

    candidates.sort(key=lambda x: x[:5])
    return [c[-1] for c in candidates[:5]]


def combine_nearby_spans(
    spans: List[Dict[str, Any]],
    limit: int = 3,
    y_tol: float = 8.0
) -> str:
    if not spans:
        return ""

    first = spans[0]
    first_bbox = first.get("bbox")
    if not first_bbox:
        return ""

    _, first_cy = center_of_bbox(first_bbox)

    same_line_parts = []
    for sp in spans[:limit]:
        sb = sp.get("bbox")
        txt = (sp.get("text") or "").strip()
        if not sb or not txt:
            continue

        _, cy = center_of_bbox(sb)
        if abs(cy - first_cy) <= y_tol:
            same_line_parts.append((sb[0], txt))

    same_line_parts.sort(key=lambda x: x[0])
    return " ".join(txt for _, txt in same_line_parts).strip()


def render_page_to_array(page: fitz.Page, scale: float = 2.0):
    mat = fitz.Matrix(scale, scale)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    if pix.n < 3:
        return None, None
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    return img, scale


def bbox_to_pixel_rect(bbox: List[float], img_shape, scale: float):
    h, w = img_shape[:2]
    x0, y0, x1, y1 = bbox
    px0 = int(max(0, min(w - 1, round(x0 * scale))))
    py0 = int(max(0, min(h - 1, round(y0 * scale))))
    px1 = int(max(0, min(w - 1, round(x1 * scale))))
    py1 = int(max(0, min(h - 1, round(y1 * scale))))
    return px0, py0, px1, py1


def median_rgb_from_pixels(pixels: np.ndarray) -> Optional[List[int]]:
    if pixels is None or len(pixels) == 0:
        return None
    med = np.median(pixels[:, :3], axis=0)
    return [int(med[0]), int(med[1]), int(med[2])] 

def sample_outside_ring_rgb(
    page: fitz.Page,
    bbox: List[float],
    scale: float = 2.0,
    ring_px: int = 4
) -> Optional[List[int]]:
    img, scale = render_page_to_array(page, scale=scale)
    if img is None:
        return None

    px0, py0, px1, py1 = bbox_to_pixel_rect(bbox, img.shape, scale)
    if px1 <= px0 or py1 <= py0:
        return None

    h, w = img.shape[:2]

    ox0 = max(0, px0 - ring_px)
    oy0 = max(0, py0 - ring_px)
    ox1 = min(w - 1, px1 + ring_px)
    oy1 = min(h - 1, py1 + ring_px)

    samples = []

    if oy0 < py0:
        top = img[oy0:py0, ox0:ox1]
        if top.size:
            samples.append(top.reshape(-1, img.shape[2]))

    if py1 < oy1:
        bottom = img[py1:oy1, ox0:ox1]
        if bottom.size:
            samples.append(bottom.reshape(-1, img.shape[2]))

    if ox0 < px0:
        left = img[py0:py1, ox0:px0]
        if left.size:
            samples.append(left.reshape(-1, img.shape[2]))

    if px1 < ox1:
        right = img[py0:py1, px1:ox1]
        if right.size:
            samples.append(right.reshape(-1, img.shape[2]))

    if not samples:
        return None

    pixels = np.vstack(samples)
    return median_rgb_from_pixels(pixels)

def annotate_graphics_non_text_contrast(
    doc: fitz.Document,
    graphics: List[Dict[str, Any]],
    scale: float = 2.0
):
    graphics_by_page: Dict[int, List[Dict[str, Any]]] = {}
    for g in graphics:
        graphics_by_page.setdefault(g["page_index"], []).append(g)

    for page_index, page_graphics in graphics_by_page.items():
        page = doc.load_page(page_index)

        for g in page_graphics:
            bbox = g.get("bbox")
            if not bbox:
                g["non_text_contrast"] = default_non_text_contrast()
                continue

            adjacent = sample_outside_ring_rgb(page, bbox, scale=scale, ring_px=4)
            if adjacent is None:
                adjacent = [255, 255, 255]
                method = "outside_ring_fallback_white"
            else:
                method = "outside_ring"

            stroke_rgb = g.get("stroke_rgb")
            fill_rgb = g.get("fill_rgb")

            contrast_against_stroke = None
            contrast_against_fill = None

            if stroke_rgb and len(stroke_rgb) >= 3:
                contrast_against_stroke = float(round(contrast_ratio(stroke_rgb, adjacent), 3))

            if fill_rgb and len(fill_rgb) >= 3:
                contrast_against_fill = float(round(contrast_ratio(fill_rgb, adjacent), 3))

            effective_contrast = contrast_against_stroke
            if effective_contrast is None:
                effective_contrast = contrast_against_fill
            elif contrast_against_fill is not None:
                effective_contrast = min(contrast_against_stroke, contrast_against_fill)

            g["non_text_contrast"] = {
                "adjacent_rgb": adjacent,
                "method": method,
                "contrast_against_stroke": contrast_against_stroke,
                "contrast_against_fill": contrast_against_fill,
                "contrast_against_border": None,
                "passes_3_1": effective_contrast >= 3.0 if effective_contrast is not None else None
            }
def sample_widget_border_rgb(
    page: fitz.Page,
    bbox: List[float],
    scale: float = 2.0,
    edge_px: int = 2
) -> Optional[List[int]]:
    img, scale = render_page_to_array(page, scale=scale)
    if img is None:
        return None

    px0, py0, px1, py1 = bbox_to_pixel_rect(bbox, img.shape, scale)
    if px1 <= px0 or py1 <= py0:
        return None

    samples = []

    top = img[py0:min(py0 + edge_px, py1), px0:px1]
    if top.size:
        samples.append(top.reshape(-1, img.shape[2]))

    bottom = img[max(py0, py1 - edge_px):py1, px0:px1]
    if bottom.size:
        samples.append(bottom.reshape(-1, img.shape[2]))

    left = img[py0:py1, px0:min(px0 + edge_px, px1)]
    if left.size:
        samples.append(left.reshape(-1, img.shape[2]))

    right = img[py0:py1, max(px0, px1 - edge_px):px1]
    if right.size:
        samples.append(right.reshape(-1, img.shape[2]))

    if not samples:
        return None

    pixels = np.vstack(samples)
    return median_rgb_from_pixels(pixels) 

def _read_mk_border_color_pikepdf(pdf_path: str, page_index: int, bbox: list, tolerance: float = 5.0) -> Optional[List[int]]:
    """
    Fallback: read border color from /MK /BC on the AcroForm field
    matching this widget by page + bbox. Returns RGB 0-255 or None.
    """
    try:
        with pikepdf.open(pdf_path) as pdf:
            acroform = pdf.Root.get("/AcroForm")
            if acroform is None:
                return None
            fields = acroform.get("/Fields")
            if not fields:
                return None

            page_height = float(pdf.pages[page_index].mediabox[3])
            # Convert PyMuPDF bbox to PDF coords for matching
            pdf_x0 = bbox[0]
            pdf_y0 = page_height - bbox[3]
            pdf_x1 = bbox[2]
            pdf_y1 = page_height - bbox[1]

            def close(a, b):
                return abs(a - b) < tolerance

            def check(node):
                if not isinstance(node, pikepdf.Dictionary):
                    try:
                        node = node.get_object()
                    except Exception:
                        return None
                # Check /Rect
                rect = node.get("/Rect")
                if rect is not None:
                    try:
                        r = [float(x) for x in rect]
                        if close(r[0], pdf_x0) and close(r[1], pdf_y0) and close(r[2], pdf_x1) and close(r[3], pdf_y1):
                            mk = node.get("/MK")
                            if isinstance(mk, pikepdf.Dictionary):
                                bc = mk.get("/BC")
                                if bc is not None and len(bc) >= 3:
                                    return [int(round(float(bc[i]) * 255)) for i in range(3)]
                            return None
                    except Exception:
                        pass
                kids = node.get("/Kids")
                if isinstance(kids, pikepdf.Array):
                    for kid in kids:
                        found = check(kid)
                        if found is not None:
                            return found
                return None

            for field_ref in fields:
                result = check(field_ref)
                if result is not None:
                    return result
    except Exception:
        pass
    return None

def annotate_widgets_non_text_contrast(
    doc: fitz.Document,
    widgets: List[Dict[str, Any]],
    scale: float = 2.0
):
    widgets_by_page: Dict[int, List[Dict[str, Any]]] = {}
    for w in widgets:
        widgets_by_page.setdefault(w["page_index"], []).append(w)

    for page_index, page_widgets in widgets_by_page.items():
        page = doc.load_page(page_index)

        for w in page_widgets:
            bbox = w.get("bbox")
            if not bbox:
                w["non_text_contrast"] = default_non_text_contrast()
                continue

            adjacent = sample_outside_ring_rgb(page, bbox, scale=scale, ring_px=4)
            border_rgb = sample_widget_border_rgb(page, bbox, scale=scale, edge_px=2)

            method = "sampled_widget_border_and_outside_ring"

            if adjacent is None:
                adjacent = [255, 255, 255]
                method += "_fallback_adjacent_white"

            contrast_against_border = None
            if border_rgb is not None:
                contrast_against_border = float(round(contrast_ratio(border_rgb, adjacent), 3))

            w["non_text_contrast"] = {
                "adjacent_rgb": adjacent,
                "method": method,
                "contrast_against_stroke": None,
                "contrast_against_fill": None,
                "contrast_against_border": contrast_against_border,
                "border_rgb": border_rgb,
                "passes_3_1": contrast_against_border >= 3.0 if contrast_against_border is not None else None
            }      
         
def graphic_overlaps_widget(graphic: Dict[str, Any], widgets: List[Dict[str, Any]], margin: float = 2.0) -> bool:
    gb = graphic.get("bbox")
    if not gb:
        return False

    for w in widgets:
        wb = w.get("bbox")
        if not wb:
            continue
        if bbox_intersects(gb, wb, margin=margin):
            return True
    return False


def is_likely_layout_or_decorative_graphic(
    graphic: Dict[str, Any],
    page_width: float,
    page_height: float
) -> bool:
    bbox = graphic.get("bbox")
    if not bbox:
        return True

    w = bbox_width(bbox)
    h = bbox_height(bbox)
    area = w * h
    page_area = page_width * page_height if page_width > 0 and page_height > 0 else 0

    fill_rgb = graphic.get("fill_rgb")
    gtype = graphic.get("type")

    # 1) huge/light panel backgrounds
    if page_area > 0 and area / page_area >= 0.025:
        if fill_rgb and all(c >= 235 for c in fill_rgb[:3]):
            return True

    # 2) white or near-white helper boxes inside panels
    if fill_rgb and all(c >= 245 for c in fill_rgb[:3]):
        if area >= 1500:
            return True

    # 3) long thin separator lines
    if gtype == "s":
        if (h <= 2.0 and w >= page_width * 0.5) or (w <= 2.0 and h >= page_height * 0.5):
            return True

    # 4) very light filled rectangles used as containers/cards
    if gtype == "f" and fill_rgb and all(c >= 240 for c in fill_rgb[:3]):
        if w >= 150 and h >= 40:
            return True

    return False

def estimate_resized_text_bbox_200(span_bbox: List[float]) -> List[float]:
    x0, y0, x1, y1 = span_bbox
    w = max(0.0, x1 - x0)
    h = max(0.0, y1 - y0)

    # Heuristic 200% enlargement:
    # width grows more strongly than height, with slight left/up padding
    return [
        x0 - 0.10 * w,
        y0 - 0.20 * h,
        x0 + 2.00 * w,
        y0 + 1.80 * h,
    ]


def bbox_exceeds_container(candidate_bbox: List[float], container: List[float], margin: float = 1.0) -> bool:
    return not bbox_contains(container, candidate_bbox, margin=margin)



def looks_like_paragraph_continuation(span1: Dict[str, Any], span2: Dict[str, Any]) -> bool:
    if span1.get("page_index") != span2.get("page_index"):
        return False

    b1 = span1.get("bbox")
    b2 = span2.get("bbox")
    if not b1 or not b2:
        return False

    # same line => not paragraph continuation
    if is_same_line(span1, span2, y_tol=8.0):
        return False

    # vertically near
    v_gap = _vertical_gap(b1, b2)

    # left aligned or strongly overlapping in x => likely wrapped paragraph lines
    left_aligned = abs(b1[0] - b2[0]) <= 25.0

    overlap = max(0.0, min(b1[2], b2[2]) - max(b1[0], b2[0]))
    min_width = min(bbox_width(b1), bbox_width(b2))
    overlap_ratio = (overlap / min_width) if min_width > 0 else 0.0

    return v_gap <= 14.0 and (left_aligned or overlap_ratio >= 0.4)


def find_line_box_container(
    span_bbox: List[float],
    page_graphics: List[Dict[str, Any]],
    tolerance: float = 3.0
) -> Optional[List[float]]:
    """
    Detect a rectangular box around text when the box is drawn using 4 separate line graphics.
    """
    x0, y0, x1, y1 = span_bbox

    horizontals = []
    verticals = []

    for g in page_graphics:
        gb = g.get("bbox")
        if not gb or g.get("type") != "s":
            continue

        w = bbox_width(gb)
        h = bbox_height(gb)

        if h <= tolerance and w > 20:
            horizontals.append(gb)

        if w <= tolerance and h > 20:
            verticals.append(gb)

    top_candidates = []
    bottom_candidates = []
    left_candidates = []
    right_candidates = []

    for hb in horizontals:
        hx0, hy0, hx1, hy1 = hb
        if hx0 <= x0 + tolerance and hx1 >= x1 - tolerance:
            cy = (hy0 + hy1) / 2.0
            if cy <= y0 + tolerance:
                top_candidates.append(hb)
            if cy >= y1 - tolerance:
                bottom_candidates.append(hb)

    for vb in verticals:
        vx0, vy0, vx1, vy1 = vb
        if vy0 <= y0 + tolerance and vy1 >= y1 - tolerance:
            cx = (vx0 + vx1) / 2.0
            if cx <= x0 + tolerance:
                left_candidates.append(vb)
            if cx >= x1 - tolerance:
                right_candidates.append(vb)

    if not (top_candidates and bottom_candidates and left_candidates and right_candidates):
        return None

    top = min(top_candidates, key=lambda b: abs(((b[1] + b[3]) / 2.0) - y0))
    bottom = min(bottom_candidates, key=lambda b: abs(((b[1] + b[3]) / 2.0) - y1))
    left = min(left_candidates, key=lambda b: abs(((b[0] + b[2]) / 2.0) - x0))
    right = min(right_candidates, key=lambda b: abs(((b[0] + b[2]) / 2.0) - x1))

    container = [
        min(left[0], left[2]),
        min(top[1], top[3]),
        max(right[0], right[2]),
        max(bottom[1], bottom[3]),
    ]

    if bbox_contains(container, span_bbox, margin=2.0):
        return container

    return None

def annotate_resize_risk(
    text_spans: List[Dict[str, Any]],
    graphics: List[Dict[str, Any]],
    widgets: List[Dict[str, Any]],
    pages: List[Dict[str, Any]]
):
    spans_by_page: Dict[int, List[Dict[str, Any]]] = {}
    graphics_by_page: Dict[int, List[Dict[str, Any]]] = {}
    widgets_by_page: Dict[int, List[Dict[str, Any]]] = {}
    page_dims = {
        p["page_index"]: (float(p["width"]), float(p["height"]))
        for p in pages
    }

    for sp in text_spans:
        spans_by_page.setdefault(sp["page_index"], []).append(sp)

    for g in graphics:
        graphics_by_page.setdefault(g["page_index"], []).append(g)

    for w in widgets:
        widgets_by_page.setdefault(w["page_index"], []).append(w)

    for sp in text_spans:
        bbox = sp.get("bbox")
        if not bbox:
            continue

        sem = sp.get("presentation_semantics", {})
        page_index = sp.get("page_index")
        page_width, page_height = page_dims.get(page_index, (0.0, 0.0))

        text_w = bbox_width(bbox)
        text_h = bbox_height(bbox)
        font_size = float(sp.get("font", {}).get("size", 0.0))
        enlarged_bbox = estimate_resized_text_bbox_200(bbox)

        nearby_text_ids = []
        nearby_graphic_ids = []
        nearby_widget_ids = []
        same_line_overlap_ids = []
        clipping_container_ids = []
        paragraph_flow_neighbors = []

        meaningful_graphics = []
        for g in graphics_by_page.get(page_index, []):
            if is_likely_layout_or_decorative_graphic(g, page_width, page_height):
                continue
            meaningful_graphics.append(g)

        # text collisions
        for other in spans_by_page.get(page_index, []):
            if other.get("id") == sp.get("id"):
                continue

            ob = other.get("bbox")
            if not ob:
                continue

            if bbox_intersects(enlarged_bbox, ob):
                nearby_text_ids.append(other.get("id"))

                if is_same_line(sp, other):
                    same_line_overlap_ids.append(other.get("id"))
                elif looks_like_paragraph_continuation(sp, other):
                    paragraph_flow_neighbors.append(other.get("id"))

        # graphic collisions and tight graphic containers
        for g in meaningful_graphics:
            gb = g.get("bbox")
            if not gb:
                continue

            if bbox_intersects(enlarged_bbox, gb):
                nearby_graphic_ids.append(g.get("id"))

            if bbox_contains(gb, bbox, margin=1.0):
                gw = bbox_width(gb)
                gh = bbox_height(gb)

                if gw <= text_w * 1.4 or gh <= text_h * 1.6:
                    if bbox_exceeds_container(enlarged_bbox, gb, margin=1.0):
                        clipping_container_ids.append(g.get("id"))

        # grouped line-box container detection
        line_box_container = find_line_box_container(bbox, meaningful_graphics)
        if line_box_container is not None:
            if bbox_exceeds_container(enlarged_bbox, line_box_container, margin=1.0):
                clipping_container_ids.append(f"line_box_{sp.get('id')}")

        # widget collisions and tight widget containers
        for w in widgets_by_page.get(page_index, []):
            wb = w.get("bbox")
            if not wb:
                continue

            if bbox_intersects(enlarged_bbox, wb):
                nearby_widget_ids.append(w.get("id"))

            if bbox_contains(wb, bbox, margin=1.0):
                ww = bbox_width(wb)
                wh = bbox_height(wb)

                if ww <= text_w * 1.6 or wh <= text_h * 1.8:
                    if bbox_exceeds_container(enlarged_bbox, wb, margin=1.0):
                        clipping_container_ids.append(w.get("id"))

        risk_score = 0

        # page overflow
        if enlarged_bbox[0] < 0 or enlarged_bbox[2] > page_width:
            risk_score += 2
        if enlarged_bbox[1] < 0 or enlarged_bbox[3] > page_height:
            risk_score += 1

        # small text slightly more vulnerable
        if font_size <= 10:
            risk_score += 1

        # strongest evidence
        if clipping_container_ids:
            risk_score += 4

        if same_line_overlap_ids:
            risk_score += min(4, len(same_line_overlap_ids) * 2)

        # moderate evidence
        risk_score += min(2, len(nearby_widget_ids))
        risk_score += min(2, len(nearby_graphic_ids))

        # count only non-paragraph text collisions
        non_paragraph_text_collisions = max(
            0,
            len(nearby_text_ids) - len(paragraph_flow_neighbors) - len(same_line_overlap_ids)
        )
        risk_score += min(2, non_paragraph_text_collisions)

        if sem.get("is_text_in_image_context", False) or sem.get("is_logo_text", False) or sem.get("is_decorative_text", False) :
            continue

        sp["resize_risk"] = {
            "font_size_pt": font_size,
            "is_small_text": font_size <= 10,
            "span_width": text_w,
            "span_height": text_h,
            "estimated_scale_200_bbox": [float(x) for x in enlarged_bbox],
            "has_nearby_text": bool(nearby_text_ids),
            "nearby_text_ids": nearby_text_ids,
            "same_line_overlap_ids": same_line_overlap_ids,
            "paragraph_flow_neighbor_ids": paragraph_flow_neighbors,
            "has_nearby_graphic": bool(nearby_graphic_ids),
            "nearby_graphic_ids": nearby_graphic_ids,
            "has_nearby_widget": bool(nearby_widget_ids),
            "nearby_widget_ids": nearby_widget_ids,
            "clipping_container_ids": clipping_container_ids,
            "would_overlap_on_scale_200": bool(
                nearby_text_ids or nearby_graphic_ids or nearby_widget_ids or clipping_container_ids
            ),
            "risk_score": risk_score,
        }


def _is_descriptive_control_name(name: str | None) -> bool:
    if not isinstance(name, str):
        return False

    n = name.strip().lower()
    if not n:
        return False

    generic_patterns = [
        r"^fld\d*$",
        r"^field\d*$",
        r"^text\d*$",
        r"^input\d*$",
        r"^textbox\d*$",
        r"^txt\d*$",
        r"^box\d*$",
        r"^form\d*$",
        r"^widget\d*$",
        r"^control\d*$",
        r"^button\d*$",
        r"^\d+$",
    ]

    for pattern in generic_patterns:
        if re.match(pattern, n):
            return False

    if len(n) < 3:
        return False

    return True


BAD_ALT_WORDS = {
    "image", "picture", "photo", "graphic", "figure",
    "icon", "logo", "video", "audio", "media"
}

def _is_suspicious_alt_text(alt: Optional[str]) -> bool:
    if alt is None:
        return False

    if alt == "":
        return False

    alt_clean = alt.strip().lower()

    alt_unquoted = alt_clean.strip('"').strip("'").strip()

    if alt_unquoted in {"", '""', "''", "/n", "\\n", "n"}:
        return True

    if alt_unquoted in BAD_ALT_WORDS:
        return True

    if re.match(r".*\.(jpg|jpeg|png|gif|bmp|svg)$", alt_unquoted):
        return True

    if re.match(r"(img|image|scan|photo)[\-_]?\d+", alt_unquoted):
        return True

    if len(alt_unquoted) <= 2:
        return True

    return False


def _blend_toward(rgb: list, target: list, amount: float) -> list:
        return [round(rgb[i] + (target[i] - rgb[i]) * amount) for i in range(3)]

def _find_accessible_color(fg_rgb: list, bg_rgb: list, target_ratio: float) -> list | None:
        if contrast_ratio(fg_rgb, bg_rgb) >= target_ratio:
            return fg_rgb
        candidates = []
        for direction in ([0, 0, 0], [255, 255, 255]):
            for step in range(1, 101):
                candidate = _blend_toward(fg_rgb, direction, step / 100)
                if contrast_ratio(candidate, bg_rgb) >= target_ratio:
                    distance = sum(abs(candidate[i] - fg_rgb[i]) for i in range(3))
                    candidates.append((distance, candidate))
                    break
        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0])
        return candidates[0][1]
