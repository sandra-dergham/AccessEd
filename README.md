# AccessEd

AccessEd is a web-based PDF accessibility checker and auto-corrector built on WCAG 2.1 guidelines. Upload a PDF and AccessEd will detect accessibility violations, generate a detailed report, produce a corrected version with automatic fixes applied, and provide an annotated copy of your document highlighting each issue in context.

## Features

- **Accessibility Detection** — checks PDFs against WCAG 2.1 criteria across four severity levels: high, medium, low, and needs review
- **Accessibility Score** — weighted 0–100 score with letter grade (A–F) based on violation severity
- **Auto-Correction** — automatically fixes what it can (missing tooltips, checkbox states, tab order, figure alt text via GPT-4o, language tags, document title, bookmarks, link labels)
- **Annotated PDF** — returns your original PDF with inline comments pinned to each violation location
- **Accessibility Report** — downloadable PDF report with score breakdown, methodology explanation, and detailed findings per violation
- **Multilingual Support** — detects language switches in Arabic, French, and other languages for WCAG 3.1.2 compliance
- **Privacy-first** — uploaded files are processed in memory and deleted immediately after analysis; nothing is stored

## Tech Stack

**Frontend** — React + TypeScript  
**Backend** — FastAPI (Python)  
**PDF Parsing** — PyMuPDF (fitz), pdfminer.six, pikepdf  
**Detection** — custom WCAG 2.1 rule engine across 3 batches  
**Correction** — pikepdf for structural fixes, GPT-4o vision for image alt text  
**Language Detection** — langdetect

## Live Demo (temporary — will redeploy on Microsoft Azure)

[access-ed-swart.vercel.app](https://access-ed-swart.vercel.app)

## Running Locally

**Backend**

```bash
cd backend
pip install -r requirements.txt
export OPENAI_API_KEY=your-key-here  # required for GPT-4o image alt text
uvicorn main:app --reload
```

**Frontend**

```bash
cd frontend
npm install
npm run dev
```

Set `VITE_API_URL=http://127.0.0.1:8000` in `frontend/.env.development`.

## Scoring Methodology

The accessibility score reflects the percentage of evaluable WCAG 2.1 criteria satisfied, weighted by severity:

| Severity | Weight | Meaning                              |
| -------- | ------ | ------------------------------------ |
| Pass     | 1.00   | No violation                         |
| Low      | 0.75   | Minor gap                            |
| Medium   | 0.25   | Confirmed violation, moderate impact |
| High     | 0.00   | Confirmed violation, severe impact   |

Criteria marked as `not_applicable` or `needs_review` are excluded from scoring.
