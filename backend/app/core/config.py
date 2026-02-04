from pathlib import Path

MAX_PDF_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB
ALLOWED_MIME_TYPES = {"application/pdf"}

TMP_UPLOAD_DIR = Path("tmp_uploads")
TMP_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
