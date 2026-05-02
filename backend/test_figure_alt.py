import sys
sys.path.insert(0, '.')

import fitz  # PyMuPDF
import pikepdf
from app.services.parsing import extract_document_json
from app.services.wcag.detector import run_wcag_detector
from app.services.corrector import apply_corrections

IMAGE_PATH = r"C:\Users\AUB\OneDrive - American University of Beirut\Desktop\jerry.png"
OUT_PDF    = "test_figure.pdf"
FIXED_PDF  = "test_figure_corrected.pdf"

# ── Step 1: Create a tagged PDF with a Figure node missing /Alt ──────────────
print("Creating test PDF with embedded image and Figure node (no /Alt)...")

doc = fitz.open()
page = doc.new_page(width=612, height=792)

# Insert the image
rect = fitz.Rect(100, 100, 400, 400)
page.insert_image(rect, filename=IMAGE_PATH)

# Save as a basic PDF first
doc.save(OUT_PDF)
doc.close()

# Now add a minimal StructTreeRoot with a Figure node (no /Alt) using pikepdf
with pikepdf.open(OUT_PDF, allow_overwriting_input=True) as pdf:
    page_obj = pdf.pages[0].obj

    # Create a Figure struct element with no /Alt
    figure_node = pdf.make_indirect(pikepdf.Dictionary(
        Type=pikepdf.Name("/StructElem"),
        S=pikepdf.Name("/Figure"),
        # deliberately NO /Alt
        K=pikepdf.Integer(0),  # MCID 0
        P=pikepdf.Name("/StructTreeRoot"),  # will fix ref below
    ))

    struct_root = pdf.make_indirect(pikepdf.Dictionary(
        Type=pikepdf.Name("/StructTreeRoot"),
        K=pikepdf.Array([figure_node]),
    ))

    # Fix parent reference
    figure_node["/P"] = struct_root

    pdf.Root["/StructTreeRoot"] = struct_root
    pdf.Root["/MarkInfo"] = pikepdf.Dictionary(Marked=pikepdf.Boolean(True))

    pdf.save(OUT_PDF)

print(f"Created {OUT_PDF}")

# ── Step 2: Run pipeline ──────────────────────────────────────────────────────
doc_json = extract_document_json(OUT_PDF)
issues   = run_wcag_detector(doc_json)

print("\n=== ISSUES DETECTED (4.1.2 only) ===")
for iss in issues:
    if iss["criterion"] == "4.1.2":
        print(f"  [{iss['severity'].upper()}] {iss['issue'][:80]}")

print()
report = apply_corrections(
    original_pdf_path=OUT_PDF,
    issues=issues,
    doc_json=doc_json,
    output_path=FIXED_PDF,
)

print("=== CORRECTION RESULTS ===")
print("Fixed:  ", report["fixed_count"])
print("Skipped:", report["skipped_count"])
print()
for r in report["corrections"]:
    print(f"  {r['status'].upper():<10} {r['criterion']:<8} {r['detail'][:80]}")

# ── Step 3: Verify /Alt was written ──────────────────────────────────────────
print("\n=== VERIFY FIGURE /Alt IN CORRECTED PDF ===")
with pikepdf.open(FIXED_PDF) as pdf2:
    struct_root = pdf2.Root.get("/StructTreeRoot")
    if struct_root is None:
        print("  No StructTreeRoot found")
    else:
        def walk(node, depth=0):
            try:
                if not isinstance(node, pikepdf.Dictionary):
                    node = node.get_object()
            except Exception:
                return
            s = node.get("/S")
            alt = node.get("/Alt")
            if s is not None:
                indent = "  " * depth
                print(f"{indent}/S={s}  /Alt={alt}")
            kids = node.get("/K")
            if isinstance(kids, pikepdf.Array):
                for kid in kids:
                    walk(kid, depth + 1)

        walk(struct_root)