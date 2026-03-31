from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import FileResponse
from uuid import uuid4
import os
import sys
import json

# Ensure backend/ is on the path so app.services.wcag resolves correctly
_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

router = APIRouter()

MAX_BYTES = 10 * 1024 * 1024  # 10 MB
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "..", "tmp_uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)


def _looks_like_pdf(upload: UploadFile) -> bool:
    ct_ok   = (upload.content_type == "application/pdf")
    name_ok = (upload.filename or "").lower().endswith(".pdf")
    return ct_ok or name_ok


@router.post("/upload")
async def upload_pdf(file: UploadFile = File(...)):
    if not file or not file.filename:
        raise HTTPException(status_code=400, detail="No file provided.")

    if not _looks_like_pdf(file):
        raise HTTPException(status_code=400, detail="Only PDF files are allowed.")

    upload_id = str(uuid4())
    out_path  = os.path.join(UPLOAD_DIR, f"{upload_id}.pdf")
    print("UPLOAD_DIR =", os.path.abspath(UPLOAD_DIR))
    print("OUT_PATH   =", os.path.abspath(out_path))

    total      = 0
    chunk_size = 1024 * 1024  # 1 MB

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
        
    # ── Step 1: Parse PDF ─────────────────────────────────────────────
    try:
        from app.services.parsing import extract_document_json
        doc_json = extract_document_json(out_path)
    except Exception as e:
       raise HTTPException(
        status_code=500,
        detail=f"Parsing failed: {e}"
    )

   # ── Step 2: Save parsed JSON ──────────────────────────────
   # will be deleted later
    try:
        json_out_path = os.path.join(UPLOAD_DIR, f"{upload_id}.json")
        with open(json_out_path, "w", encoding="utf-8") as f:
             json.dump(doc_json, f, ensure_ascii=False, indent=2)
    except Exception as e:
        raise HTTPException(
        status_code=500,
        detail=f"Failed to save JSON: {e}"
    )

    # ── Step 3: Run WCAG detector ─────────────────────────────────────────────
    try:
        from app.services.wcag.detector import run_wcag_detector
        issues = run_wcag_detector(doc_json)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"WCAG detection failed: {e}"
        )

    # ── Step 4: Build report ──────────────────────────────────────────────────
    try:
        from app.services.wcag.report_builder import build_report
        document_meta = doc_json.get("document", {}).get("metadata", {})
        report = build_report(document_meta, issues)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Report building failed: {e}"
        )

# ── Step 5: Generate PDF report ───────────────────────────────────
    try:
        from app.services.wcag.report_builder import build_pdf_report
        pdf_out_path = os.path.join(UPLOAD_DIR, f"{upload_id}_report.pdf")
        build_pdf_report(report,pdf_out_path)
    except Exception as e:
        raise HTTPException(
            status_code=500,
        detail=f"PDF generation failed: {e}"
    )
    # ── Step 6: Return response ───────────────────────────────────────────────
    return {
        "upload_id":         upload_id,
        "original_filename": file.filename,
        "size_bytes":        total,
        "status":            "analysed",
        "report":            report,
        "pdf_report_path": pdf_out_path,
    }
@router.get("/uploads/{upload_id}/report")
async def download_report(upload_id: str):
    pdf_path = os.path.join(UPLOAD_DIR, f"{upload_id}_report.pdf")

    if not os.path.exists(pdf_path):
        raise HTTPException(
            status_code=404,
            detail="Report not found. The upload ID may be invalid or the file has expired.",
        )

    return FileResponse(
        path=pdf_path,
        media_type="application/pdf",
        filename=f"report-{upload_id}.pdf",
    )