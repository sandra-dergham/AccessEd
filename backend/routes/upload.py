from fastapi import APIRouter, UploadFile, File, HTTPException
from uuid import uuid4
import os

router = APIRouter()

MAX_BYTES = 10 * 1024 * 1024  # 10 MB
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "..", "tmp_uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)


def _looks_like_pdf(upload: UploadFile) -> bool:
    # MIME type can be wrong sometimes; accept if either MIME or extension suggests PDF
    ct_ok = (upload.content_type == "application/pdf")
    name_ok = (upload.filename or "").lower().endswith(".pdf")
    return ct_ok or name_ok


@router.post("/upload")
async def upload_pdf(file: UploadFile = File(...)):
    if not file or not file.filename:
        raise HTTPException(status_code=400, detail="No file provided.")

    if not _looks_like_pdf(file):
        raise HTTPException(status_code=400, detail="Only PDF files are allowed.")

    upload_id = str(uuid4())
    out_path = os.path.join(UPLOAD_DIR, f"{upload_id}.pdf")
    print("UPLOAD_DIR =", os.path.abspath(UPLOAD_DIR))
    print("OUT_PATH   =", os.path.abspath(out_path))

    total = 0
    chunk_size = 1024 * 1024  # 1MB

    try:
        with open(out_path, "wb") as f:
            while True:
                chunk = await file.read(chunk_size)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_BYTES:
                    f.close()
                    try:
                        os.remove(out_path)
                    except OSError:
                        pass
                    raise HTTPException(status_code=413, detail="File too large. Max is 10 MB.")
                f.write(chunk)
    finally:
        await file.close()

    # Quick signature check: PDF files start with %PDF-
    with open(out_path, "rb") as f:
        if f.read(5) != b"%PDF-":
            try:
                os.remove(out_path)
            except OSError:
                pass
            raise HTTPException(status_code=400, detail="Invalid PDF file.")

    return {
        "upload_id": upload_id,
        "original_filename": file.filename,
        "size_bytes": total,
        "status": "uploaded",
    }
