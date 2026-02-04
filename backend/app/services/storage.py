import uuid
from pathlib import Path
from fastapi import UploadFile, HTTPException
from app.core.config import MAX_PDF_SIZE_BYTES, ALLOWED_MIME_TYPES, TMP_UPLOAD_DIR

async def save_pdf_temporarily(upload: UploadFile) -> Path:
    # 1) PDF-only check (server-side)
    if upload.content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(status_code=400, detail="Only PDF files are allowed.")

    # 2) Read bytes (safe because max is 10MB)
    data = await upload.read()

    # 3) Size check
    if len(data) > MAX_PDF_SIZE_BYTES:
        raise HTTPException(status_code=400, detail="File too large. Max is 10 MB.")

    if len(data) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    # 4) Save to temp folder with unique filename
    filename = f"{uuid.uuid4().hex}.pdf"
    path = TMP_UPLOAD_DIR / filename
    path.write_bytes(data)

    return path

def delete_temp_file(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except Exception:
        pass
