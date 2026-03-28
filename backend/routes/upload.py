from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import StreamingResponse
from uuid import uuid4
import os
import sys
import subprocess
import json
import io

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

    # ── Step 1: Run parser ────────────────────────────────────────────────────
    json_out_path = os.path.join(UPLOAD_DIR, f"{upload_id}.json")

    # Use the venv Python explicitly to ensure all packages are available
    venv_python = os.path.join(
        os.path.dirname(__file__), "..", "venv", "Scripts", "python.exe"
    )
    if not os.path.exists(venv_python):
        # Fallback for non-Windows or different venv structure
        venv_python = os.path.join(
            os.path.dirname(__file__), "..", "venv", "bin", "python"
        )
    if not os.path.exists(venv_python):
        venv_python = sys.executable  # last resort fallback

    parsing_script = os.path.join(
        os.path.dirname(__file__), "..", "app", "services", "parsing.py"
    )

    result = subprocess.run(
        [
            venv_python,
            os.path.abspath(parsing_script),
            os.path.abspath(out_path),
            "--out", os.path.abspath(json_out_path)
        ],
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail=f"Parsing failed: {result.stderr or result.stdout}"
        )

    # ── Step 2: Load parsed JSON ──────────────────────────────────────────────
    try:
        with open(json_out_path, "r", encoding="utf-8") as f:
            doc_json = json.load(f)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to load parsed JSON: {e}"
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
    # ── Step 4b: Save report JSON to disk ─────────────────────────────────────
    report_json_path = os.path.join(UPLOAD_DIR, f"{upload_id}_report.json")
    try:
        with open(report_json_path, "w", encoding="utf-8") as f:
            json.dump(report, f)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to save report: {e}"
        )

    # ── Step 5: Return response ───────────────────────────────────────────────
    return {
        "upload_id":         upload_id,
        "original_filename": file.filename,
        "size_bytes":        total,
        "status":            "analysed",
        "report":            report,
    }

@router.get("/uploads/{upload_id}/report")
async def download_report(upload_id: str):
    # ── Step 1: Load the saved report JSON ────────────────────────────────────
    report_json_path = os.path.join(UPLOAD_DIR, f"{upload_id}_report.json")

    if not os.path.exists(report_json_path):
        raise HTTPException(
            status_code=404,
            detail="Report not found. The upload ID may be invalid or the file has expired."
        )

    try:
        with open(report_json_path, "r", encoding="utf-8") as f:
            report_json = json.load(f)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to read report: {e}"
        )

    # ── Step 2: Generate the PDF ──────────────────────────────────────────────
    try:
        from app.services.wcag.report_builder import build_report_pdf
        pdf_bytes = build_report_pdf(report_json)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate PDF report: {e}"
        )

    # ── Step 3: Return as a downloadable PDF file ─────────────────────────────
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename=report_{upload_id}.pdf"
        }
    )