from fastapi import APIRouter, UploadFile, File
from app.services.storage import save_pdf_temporarily, delete_temp_file
from app.services.pdf_validator import validate_pdf_structure
from app.services.corrector import apply_corrections
router = APIRouter()

@router.post("/upload")
async def upload_pdf(file: UploadFile = File(...)):
    # Save temporarily
    path = await save_pdf_temporarily(file)

    try:
        # Validate structure
        validate_pdf_structure(path)

        # Success: return temp path (later Person 3 will use it)
        return {"message": "Uploaded + validated", "temp_path": str(path)}

    except Exception:
        # Fail: delete file to meet privacy requirement
        delete_temp_file(path)
        raise
