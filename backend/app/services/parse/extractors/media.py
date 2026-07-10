# extractors/media.py
import subprocess
from pathlib import Path
from typing import Any, Dict, List,Optional
import os
import tempfile
import json 
import fitz
import pikepdf
from ...utils.geometry_helper import pdfminer_bbox_to_pymupdf_bbox

from ...utils.patterns import (
    AUDIO_DESC_NEGATIVE_RE,
    AUDIO_DESC_POSITIVE_RE,
    CAPTION_NEGATIVE_RE,
    CAPTION_POSITIVE_RE,
    LIVE_RE,
    MEDIA_ALT_NEGATIVE_RE,
    MEDIA_ALT_POSITIVE_RE,
    TRANSCRIPT_NEGATIVE_RE,
    TRANSCRIPT_POSITIVE_RE,
    AUDIO_EXTS,
    VIDEO_EXTS
)

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
    

def classify_by_filename(filename: Optional[str]) -> str:
    name = (filename or "").lower()
    for ext in AUDIO_EXTS:
        if name.endswith(ext):
            return "audio_only"
    for ext in VIDEO_EXTS:
        if name.endswith(ext):
            return "video_only"
    return "unknown"

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


def _classify_media_kind(filename: Optional[str], mime_type: Optional[str] = None) -> str:
    name = (filename or "").lower()
    mime = (mime_type or "").lower()

    if any(name.endswith(ext) for ext in AUDIO_EXTS) or mime.startswith("audio/"):
        return "audio"

    if any(name.endswith(ext) for ext in VIDEO_EXTS) or mime.startswith("video/"):
        return "video"

    return "unknown"


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