from PIL import Image
import pytesseract
import io
import os
pytesseract.pytesseract.tesseract_cmd = os.environ.get(
    "TESSERACT_CMD",
    r"C:\Program Files\Tesseract-OCR\tesseract.exe"
)

def ocr_image_bytes(img_bytes: bytes, lang: str = "eng"):
    """Returns (text, confidence)."""
    try:
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    except Exception:
        return None, None

    try:
        text = pytesseract.image_to_string(img, lang=lang).strip()
    except Exception:
        return None, None

    conf = None
    try:
        data  = pytesseract.image_to_data(img, lang=lang, output_type=pytesseract.Output.DICT)
        confs = []
        for c in data.get("conf", []):
            try:
                c = float(c)
                if c >= 0:
                    confs.append(c)
            except Exception:
                pass
        if confs:
            conf = sum(confs) / len(confs)
    except Exception:
        conf = None

    return text if text else None, conf


def run_ocr_on_image_occurrences(image_occurrences, asset_bytes_global, min_px: int = 40_000):
    for occ in image_occurrences:
        asset_id = occ["asset_id"]
        b        = asset_bytes_global.get(asset_id)
        if not b:
            continue

        try:
            img  = Image.open(io.BytesIO(b))
            w, h = img.size
            if (w * h) < min_px:
                continue
        except Exception:
            pass

        text, conf           = ocr_image_bytes(b, lang="eng")
        occ["ocr_text"]      = text
        occ["ocr_confidence"] = conf