import math
from typing import List,Optional,Dict,Any
import fitz

import numpy as np 

#helpers
def is_bold_font(font: Dict[str, Any]) -> bool:
    name  = (font.get("name") or "").lower()
    flags = font.get("flags")
    if "bold" in name:
        return True
    if isinstance(flags, int) and (flags & 16):
        return True
    return False


def is_large_text(font: Dict[str, Any]) -> bool:
    """
    Returns True if the font qualifies as 'large text' per WCAG:
    18pt normal weight, or 14pt bold.
    Accepts a font dict with 'size' and optionally 'flags'/'name'.
    """
    size = float(font.get("size", 0.0))
    bold = is_bold_font(font)
    return size >= 18.0 or (bold and size >= 14.0)


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

def _is_redish(rgb: list[int]) -> bool:
    if not rgb or len(rgb) < 3:
        return False
    r, g, b = rgb[:3]
    return r >= 150 and r >= g + 40 and r >= b + 40

def _blend_toward(rgb: list, target: list, amount: float) -> list:
        return [round(rgb[i] + (target[i] - rgb[i]) * amount) for i in range(3)]




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


def float_rgb_to_int(rgb):
    if rgb is None:
        return None
    if isinstance(rgb, (list, tuple)) and len(rgb) >= 3:
        return [
            int(max(0, min(255, round(rgb[0] * 255)))),
            int(max(0, min(255, round(rgb[1] * 255)))),
            int(max(0, min(255, round(rgb[2] * 255))))
        ]
    return None

 
def int_color_to_rgb(color_int: Optional[int]) -> List[int]:
    if color_int is None or color_int < 0:
        return [0, 0, 0]
    r = (color_int >> 16) & 255
    g = (color_int >> 8) & 255
    b = color_int & 255
    return [r, g, b]



def estimate_background_rgb_for_bbox(
    page: fitz.Page, bbox: List[float], scale: float = 2.0, grid: int = 8
) -> Optional[List[int]]:
    try:
        mat = fitz.Matrix(scale, scale)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        if pix.n < 3:
            return None
    except Exception:
        return None

    x0, y0, x1, y1 = bbox
    w = x1 - x0
    h = y1 - y0

    # Sample AROUND the bbox, never under it.
    # This prevents dark text pixels from contaminating the background estimate
    # when re-scanning an already-corrected PDF.
    pad   = max(3.0, min(w, h) * 0.5)   # how far outside the bbox to sample
    strip = max(2.0, min(w, h) * 0.3)   # thickness of the sampling strip

    sample_regions = [
        (x0,           y0 - pad - strip, x1,           y0 - pad),  # above
        (x0,           y1 + pad,         x1,           y1 + pad + strip),  # below
        (x0 - pad - strip, y0,           x0 - pad,     y1),  # left
        (x1 + pad,     y0,               x1 + pad + strip, y1),  # right
    ]

    samples = []

    for (rx0, ry0, rx1, ry1) in sample_regions:
        px0 = int(max(0, min(pix.width  - 1, round(rx0 * scale))))
        py0 = int(max(0, min(pix.height - 1, round(ry0 * scale))))
        px1 = int(max(0, min(pix.width  - 1, round(rx1 * scale))))
        py1 = int(max(0, min(pix.height - 1, round(ry1 * scale))))

        if px1 <= px0 or py1 <= py0:
            continue

        xs = np.linspace(px0, px1 - 1, grid).astype(int)
        ys = np.linspace(py0, py1 - 1, grid).astype(int)

        for yy in ys:
            for xx in xs:
                samples.append(img[yy, xx, :3])

    # If all strips were clipped (bbox at page edge), fall back to sampling
    # the brightest pixels in a slightly expanded region around the bbox —
    # still avoiding the exact bbox area.
    if not samples:
        expand = 6
        ex0 = int(max(0, round(x0 * scale) - expand))
        ey0 = int(max(0, round(y0 * scale) - expand))
        ex1 = int(min(pix.width  - 1, round(x1 * scale) + expand))
        ey1 = int(min(pix.height - 1, round(y1 * scale) + expand))

        tx0 = int(max(0, round(x0 * scale)))
        ty0 = int(max(0, round(y0 * scale)))
        tx1 = int(min(pix.width  - 1, round(x1 * scale)))
        ty1 = int(min(pix.height - 1, round(y1 * scale)))

        for yy in range(ey0, ey1 + 1):
            for xx in range(ex0, ex1 + 1):
                if tx0 <= xx <= tx1 and ty0 <= yy <= ty1:
                    continue  # skip the actual text area
                samples.append(img[yy, xx, :3])

    if not samples:
        return [255, 255, 255]  # hard fallback

    samples = np.array(samples)

    # Use the BRIGHTEST quartile, not the median.
    # Background is almost always the lightest region; text pixels are dark.
    # Taking the median risks including dark neighboring text or graphics.
    luminances = 0.2126 * samples[:, 0] + 0.7152 * samples[:, 1] + 0.0722 * samples[:, 2]
    threshold  = np.percentile(luminances, 75)  # top 25% brightest
    bright     = samples[luminances >= threshold]

    med = np.median(bright, axis=0)
    return [int(med[0]), int(med[1]), int(med[2])]


def compute_contrast_for_spans(
    doc: fitz.Document, text_spans: List[Dict[str, Any]], scale: float = 2.0
):
    """
    Adds span["background_estimate"] and span["contrast"] to each span.
    """
    spans_by_page: Dict[int, List[Dict[str, Any]]] = {}
    for s in text_spans:
        spans_by_page.setdefault(s["page_index"], []).append(s)

    for page_index, spans in spans_by_page.items():
        page = doc.load_page(page_index)

        for sp in spans:
            bbox = sp.get("bbox")
            if not bbox:
                continue

            fg   = sp.get("color", {}).get("fill_rgb", [0, 0, 0])
            font = sp.get("font", {})
            large = is_large_text(font)   

            bg = estimate_background_rgb_for_bbox(page, bbox, scale=scale, grid=8)
            if bg is None:
                bg     = [255, 255, 255]
                method = "fallback_white"
            else:
                method = "sampled_median"

            if not isinstance(fg, list) or len(fg) < 3:
                continue

            ratio = contrast_ratio(fg, bg)

            sp["background_estimate"] = {
                "bg_rgb":  bg,
                "method":  method,
                "samples": 64
            }
            sp["contrast"] = {
                "ratio":              float(round(ratio, 3)),
                "passes_4_5_1":       ratio >= 4.5,
                "passes_3_1_large":   ratio >= 3.0 if large else None,
                "large_text_assumed": large
            }

