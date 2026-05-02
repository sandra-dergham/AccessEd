import sys
sys.path.insert(0, '.')

import pikepdf
from app.services.parsing import extract_document_json
from app.services.wcag.detector import run_wcag_detector
from app.services.corrector import apply_corrections

# ── Create test PDF ──────────────────────────────────────────
pdf = pikepdf.new()

page = pdf.make_indirect(pikepdf.Dictionary(
    Type=pikepdf.Name('/Page'),
    MediaBox=pikepdf.Array([0, 0, 612, 792]),
))
pdf.pages.append(pikepdf.Page(page))

field_no_name = pdf.make_indirect(pikepdf.Dictionary(
    Type=pikepdf.Name('/Annot'),
    Subtype=pikepdf.Name('/Widget'),
    FT=pikepdf.Name('/Tx'),
    Rect=pikepdf.Array([50, 700, 200, 720]),
))

field_with_name = pdf.make_indirect(pikepdf.Dictionary(
    Type=pikepdf.Name('/Annot'),
    Subtype=pikepdf.Name('/Widget'),
    FT=pikepdf.Name('/Tx'),
    T=pikepdf.String('first_name'),
    Rect=pikepdf.Array([50, 650, 200, 670]),
))

checkbox = pdf.make_indirect(pikepdf.Dictionary(
    Type=pikepdf.Name('/Annot'),
    Subtype=pikepdf.Name('/Widget'),
    FT=pikepdf.Name('/Btn'),
    T=pikepdf.String('agree_terms'),
    Rect=pikepdf.Array([50, 600, 70, 620]),
))

pdf.Root['/AcroForm'] = pdf.make_indirect(pikepdf.Dictionary(
    Fields=pikepdf.Array([field_no_name, field_with_name, checkbox]),
))
pdf.pages[0]['/Annots'] = pikepdf.Array([field_no_name, field_with_name, checkbox])

pdf.save('test_batch3.pdf')
pdf.close()
print('Created test_batch3.pdf')

# ── Run pipeline ─────────────────────────────────────────────
doc_json = extract_document_json('test_batch3.pdf')
issues = run_wcag_detector(doc_json)

print('\n=== ISSUES DETECTED ===')
for iss in issues:
    if iss['criterion'] in {'2.1.1', '3.3.2', '4.1.2'}:
        print(f"  [{iss['severity'].upper()}] {iss['criterion']} - {iss['issue'][:70]}")

print()
report = apply_corrections(
    original_pdf_path='test_batch3.pdf',
    issues=issues,
    doc_json=doc_json,
    output_path='test_batch3_corrected.pdf',
)

print('=== CORRECTION RESULTS ===')
print('Fixed:  ', report['fixed_count'])
print('Skipped:', report['skipped_count'])
print()
for r in report['corrections']:
    if r['criterion'] in {'2.1.1', '3.3.2', '4.1.2'}:
        print(f"  {r['status'].upper():<10} {r['criterion']:<8} {r['detail'][:80]}")

# ── Verify written values ────────────────────────────────────
print('\n=== VERIFY CORRECTED PDF ===')
pdf2 = pikepdf.open('test_batch3_corrected.pdf')

print('Tab order:')
for i, p in enumerate(pdf2.pages):
    print(f'  Page {i}: /Tabs = {p.get("/Tabs")}')

print('\nForm fields:')
acroform = pdf2.Root.get('/AcroForm')
if acroform:
    for ref in acroform.get('/Fields', []):
        try:
            obj = pdf2.get_object(ref.objgen)
            t   = obj.get('/T')
            tu  = obj.get('/TU')
            ft  = obj.get('/FT')
            as_ = obj.get('/AS')
            print(f'  /T={t}  /TU={tu}  /FT={ft}  /AS={as_}')
        except Exception as e:
            print(f'  Error: {e}')

pdf2.close()