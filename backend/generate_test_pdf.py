"""
AccessEd – Test PDF Generator
==============================
Generates a PDF with known accessibility violations to test batch3 rules.

Violations built in:
  - Untagged PDF (no /StructTreeRoot)                    → 2.1.1-A high, 4.1.2-A high
  - AcroForm fields with no /Tabs on any page            → 2.1.1-B medium
  - Text field with no /TU and no /T                     → 3.3.2 high, 4.1.2-E high
  - Text field with auto-generated /T only (numeric)     → 3.3.2 medium
  - Text field with readable /T only (no /TU)            → 3.3.2 low
  - Checkbox with no /AS appearance state                → 4.1.2-F high
  - Validation action on one field                       → 3.3.1 needs_review
  - Submit action                                        → 3.3.4 needs_review

Run:
    python generate_test_pdf.py
Output:
    test_violations.pdf
"""

import pikepdf
from pikepdf import Dictionary, Array, Name, String


def build_test_pdf(out_path: str = "test_violations.pdf"):
    pdf = pikepdf.Pdf.new()

    # ── Page ─────────────────────────────────────────────────────────────────
    page = pikepdf.Page(
        Dictionary(
            Type=Name("/Page"),
            MediaBox=Array([0, 0, 612, 792]),
            Resources=Dictionary(
                Font=Dictionary(
                    F1=Dictionary(
                        Type=Name("/Font"),
                        Subtype=Name("/Type1"),
                        BaseFont=Name("/Helvetica"),
                    )
                )
            ),
            Contents=pdf.make_stream(
                b"BT /F1 14 Tf 50 750 Td (AccessEd Test PDF - Violations) Tj ET"
            ),
        )
    )
    pdf.pages.append(page)

    # ── AcroForm fields ───────────────────────────────────────────────────────
    fields = []

    # Field 1: no /T and no /TU → 3.3.2 high, 4.1.2-E high
    field1 = pdf.make_indirect(
        Dictionary(
            Type=Name("/Annot"),
            Subtype=Name("/Widget"),
            FT=Name("/Tx"),
            Ff=0,
            Rect=Array([50, 680, 250, 700]),
            P=page.obj,
        )
    )
    fields.append(field1)
    page.obj["/Annots"] = Array([field1])

    # Field 2: /T only, numeric (auto-generated) → 3.3.2 medium
    field2 = pdf.make_indirect(
        Dictionary(
            Type=Name("/Annot"),
            Subtype=Name("/Widget"),
            FT=Name("/Tx"),
            Ff=0,
            T=String("1042"),
            Rect=Array([50, 640, 250, 660]),
            P=page.obj,
        )
    )
    fields.append(field2)

    # Field 3: /T only, readable → 3.3.2 low
    field3 = pdf.make_indirect(
        Dictionary(
            Type=Name("/Annot"),
            Subtype=Name("/Widget"),
            FT=Name("/Tx"),
            Ff=0,
            T=String("fullname"),
            Rect=Array([50, 600, 250, 620]),
            P=page.obj,
        )
    )
    fields.append(field3)

    # Field 4: checkbox with no /AS → 4.1.2-F high
    field4 = pdf.make_indirect(
        Dictionary(
            Type=Name("/Annot"),
            Subtype=Name("/Widget"),
            FT=Name("/Btn"),
            Ff=0,
            T=String("agree"),
            TU=String("I agree to the terms"),
            Rect=Array([50, 560, 70, 580]),
            P=page.obj,
        )
    )
    fields.append(field4)

    # Field 5: text field with /TU + validation action → 3.3.1 needs_review
    field5 = pdf.make_indirect(
        Dictionary(
            Type=Name("/Annot"),
            Subtype=Name("/Widget"),
            FT=Name("/Tx"),
            Ff=0,
            T=String("email"),
            TU=String("Email address"),
            Rect=Array([50, 520, 250, 540]),
            P=page.obj,
            AA=Dictionary(
                V=Dictionary(
                    Type=Name("/Action"),
                    S=Name("/JavaScript"),
                    JS=String("if (!event.value.match(/@/)) app.alert('Invalid email');"),
                )
            ),
        )
    )
    fields.append(field5)

    # Field 6: submit button → 3.3.4 needs_review
    field6 = pdf.make_indirect(
        Dictionary(
            Type=Name("/Annot"),
            Subtype=Name("/Widget"),
            FT=Name("/Btn"),
            Ff=pikepdf.Integer(65536),  # bit 17 = push button
            T=String("submit"),
            TU=String("Submit form"),
            Rect=Array([50, 480, 150, 500]),
            P=page.obj,
            A=Dictionary(
                Type=Name("/Action"),
                S=Name("/SubmitForm"),
                F=Dictionary(
                    Type=Name("/Filespec"),
                    F=String("https://example.com/submit"),
                    FS=Name("/URL"),
                ),
                Flags=pikepdf.Integer(0),
            ),
        )
    )
    fields.append(field6)

    # ── AcroForm (no /Tabs on page → 2.1.1-B medium) ─────────────────────────
    # Note: intentionally not adding /Tabs to the page
    pdf.Root["/AcroForm"] = Dictionary(
        Fields=Array(fields),
        DR=Dictionary(),
        DA=String("/Helvetica 12 Tf 0 g"),
    )

    # ── No tag tree → untagged PDF (2.1.1-A high, 4.1.2-A high) ──────────────
    # Simply don't add /StructTreeRoot — pikepdf.Pdf.new() has none by default

    pdf.save(out_path)
    print(f"Test PDF written to: {out_path}")
    print()
    print("Expected violations:")
    print("  [HIGH]         2.1.1   - Untagged PDF, interactive elements not discoverable")
    print("  [MEDIUM]       2.1.1   - AcroForm fields, no /Tabs on any page")
    print("  [PASS]         2.1.2   - No JavaScript trap (JS exists but no trap)")
    print("  [NEEDS_REVIEW] 2.1.4   - JavaScript present")
    print("  [NEEDS_REVIEW] 2.2.1   - JavaScript present")
    print("  [NEEDS_REVIEW] 2.2.2   - JavaScript present")
    print("  [NEEDS_REVIEW] 2.3.1   - JavaScript present")
    print("  [NEEDS_REVIEW] 3.3.1   - Validation action on email field")
    print("  [HIGH]         3.3.2   - field1 has no /T and no /TU")
    print("  [MEDIUM]       3.3.2   - field2 has /T='1042' (numeric, unreadable)")
    print("  [LOW]          3.3.2   - field3 has /T='fullname' (readable but no /TU)")
    print("  [NEEDS_REVIEW] 3.3.3   - Validation action on email field")
    print("  [NEEDS_REVIEW] 3.3.4   - Submit action detected")
    print("  [HIGH]         4.1.2   - Untagged PDF")
    print("  [HIGH]         4.1.2   - Checkbox with no /AS appearance state")


if __name__ == "__main__":
    build_test_pdf("test_violations.pdf")