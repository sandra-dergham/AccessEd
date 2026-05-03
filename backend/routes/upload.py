from fastapi import APIRouter, UploadFile, File, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse, FileResponse
from uuid import uuid4
import os
import sys
import json
import io
import logging
from app.services.corrector import apply_corrections

# Ensure backend/ is on the path so app.services.wcag resolves correctly
_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

router = APIRouter()
logger = logging.getLogger(__name__)

MAX_BYTES = 10 * 1024 * 1024  # 10 MB
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "..", "tmp_uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)


def _looks_like_pdf(upload: UploadFile) -> bool:
    ct_ok   = (upload.content_type == "application/pdf")
    name_ok = (upload.filename or "").lower().endswith(".pdf")
    return ct_ok or name_ok


def _cleanup(*paths):
    for path in paths:
        try:
            if path and os.path.exists(path):
                os.remove(path)
                logger.info("Deleted temp file: %s", path)
        except Exception as e:
            logger.warning("Could not delete %s: %s", path, e)


@router.post("/upload")
async def upload_pdf(file: UploadFile = File(...), background_tasks: BackgroundTasks):
    if not file or not file.filename:
        raise HTTPException(status_code=400, detail="No file provided.")

    if not _looks_like_pdf(file):
        raise HTTPException(status_code=400, detail="Only PDF files are allowed.")

    upload_id = str(uuid4())
    out_path  = os.path.join(UPLOAD_DIR, f"{upload_id}.pdf")

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
        _cleanup(out_path)
        raise HTTPException(status_code=500, detail=f"Parsing failed: {e}")

    # ── Step 2: Save parsed JSON ──────────────────────────────────────
    json_out_path = os.path.join(UPLOAD_DIR, f"{upload_id}.json")
    try:
        with open(json_out_path, "w", encoding="utf-8") as f:
            json.dump(doc_json, f, ensure_ascii=False, indent=2)
    except Exception as e:
        _cleanup(out_path)
        raise HTTPException(status_code=500, detail=f"Failed to save JSON: {e}")

    # ── Step 3: Delete original PDF now that JSON is saved ────────────
    _cleanup(out_path)

    # ── Step 4: Run WCAG detector ─────────────────────────────────────
    try:
        from app.services.wcag.detector import run_wcag_detector
        issues = run_wcag_detector(doc_json)
    except Exception as e:
        _cleanup(json_out_path)
        raise HTTPException(status_code=500, detail=f"WCAG detection failed: {e}")

    # ── Step 5: Build report ──────────────────────────────────────────
    try:
        from app.services.wcag.report_builder import build_report
        document_meta = doc_json.get("document", {}).get("metadata", {})
        report = build_report(document_meta, issues)
    except Exception as e:
        _cleanup(json_out_path)
        raise HTTPException(status_code=500, detail=f"Report building failed: {e}")

    # ── Step 5b: Save report JSON ─────────────────────────────────────
    report_json_path = os.path.join(UPLOAD_DIR, f"{upload_id}_report.json")
    try:
        with open(report_json_path, "w", encoding="utf-8") as f:
            json.dump(report, f)
    except Exception as e:
        _cleanup(json_out_path)
        raise HTTPException(status_code=500, detail=f"Failed to save report: {e}")

    # ── Step 6: Generate PDF report ───────────────────────────────────
    pdf_out_path = os.path.join(UPLOAD_DIR, f"{upload_id}_report.pdf")
    try:
        from app.services.wcag.report_builder import build_pdf_report
        build_pdf_report(report, pdf_out_path)
    except Exception as e:
        _cleanup(json_out_path, report_json_path)
        raise HTTPException(status_code=500, detail=f"PDF generation failed: {e}")

    # ── Step 7: Apply corrections ─────────────────────────────────────
    corrected_path = os.path.join(UPLOAD_DIR, f"{upload_id}_corrected.pdf")
    try:
        correction_result = apply_corrections(
            original_pdf_path=pdf_out_path,
            issues=issues,
            doc_json=doc_json,
            output_path=corrected_path,
        )
    except Exception as e:
        correction_result = {"status": "failed", "error": str(e)}
        corrected_path = None

    # ── Cleanup: delete JSON files now, keep PDFs for download ────────
    if background_tasks is not None:
        background_tasks.add_task(_cleanup, json_out_path)

    return {
        "upload_id":         upload_id,
        "original_filename": file.filename,
        "size_bytes":        total,
        "status":            "analysed",
        "report":            report,
        "pdf_report_path":   pdf_out_path,
        "corrected_path":    corrected_path,
    }


@router.get("/uploads/{upload_id}/report")
async def download_report(upload_id: str, background_tasks: BackgroundTasks):
    report_json_path = os.path.join(UPLOAD_DIR, f"{upload_id}_report.json")
    pdf_out_path     = os.path.join(UPLOAD_DIR, f"{upload_id}_report.pdf")

    if not os.path.exists(pdf_out_path):
        raise HTTPException(
            status_code=404,
            detail="Report not found. The upload ID may be invalid or the file has expired."
        )

    with open(pdf_out_path, "rb") as f:
        pdf_bytes = f.read()

    background_tasks.add_task(_cleanup, pdf_out_path, report_json_path)

    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename=report_{upload_id}.pdf"
        },
    )


@router.get("/uploads/{upload_id}/corrected")
async def download_corrected(upload_id: str, background_tasks: BackgroundTasks):
    corrected_path = os.path.join(UPLOAD_DIR, f"{upload_id}_corrected.pdf")

    if not os.path.exists(corrected_path):
        raise HTTPException(
            status_code=404,
            detail="Corrected PDF not found. The upload ID may be invalid or the file has expired.",
        )

    background_tasks.add_task(_cleanup, corrected_path)

    return FileResponse(
        path=corrected_path,
        media_type="application/pdf",
        filename=f"corrected-{upload_id}.pdf",
    )