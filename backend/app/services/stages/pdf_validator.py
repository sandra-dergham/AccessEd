from pathlib import Path
from fastapi import HTTPException
from pypdf import PdfReader
from pypdf.errors import PdfReadError

def validate_pdf_structure(path: Path) -> None:
    """
    Must confirm:
      - readable
      - not encrypted
      - not corrupted
      - parsable
    """
    try:
        reader = PdfReader(str(path))

        # Encrypted?
        if reader.is_encrypted:
            raise HTTPException(status_code=400, detail="PDF is encrypted/password-protected.")

        # Force parse to detect corruption
        num_pages = len(reader.pages)
        if num_pages <= 0:
            raise HTTPException(status_code=400, detail="PDF has no pages (invalid file).")

        # Touch first page for deeper read
        _ = reader.pages[0]

    except PdfReadError:
        raise HTTPException(status_code=400, detail="PDF is corrupted or unreadable.")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="PDF validation failed (unreadable/corrupted).")
