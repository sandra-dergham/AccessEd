import sys
sys.path.insert(0, ".")

import pikepdf

from app.services.parsing import extract_document_json
from app.services.wcag.detector import run_wcag_detector
from app.services.corrector import apply_corrections


def print_relevant(report, criteria):
    print("\n=== CORRECTION RESULTS ===")
    print("Fixed:  ", report["fixed_count"])
    print("Skipped:", report["skipped_count"])
    print("Flagged:", report["flagged_count"])

    for r in report["corrections"]:
        if r["criterion"] in criteria:
            print(
                f"  {r['status'].upper():<12} "
                f"{r['criterion']:<8} "
                f"{r.get('issue', ''):<40} "
                f"{r.get('detail', '')[:80]}"
            )


def verify_fields(path):
    print("\n=== VERIFY PDF FIELDS ===")
    pdf = pikepdf.open(path)
    acroform = pdf.Root.get("/AcroForm")
    if not acroform:
        print("  No AcroForm found")
        pdf.close()
        return
    for ref in acroform.get("/Fields", []):
        obj = pdf.get_object(ref.objgen)
        t   = obj.get("/T")
        tu  = obj.get("/TU")
        mk  = obj.get("/MK")
        bc  = mk.get("/BC") if mk else None
        print(f"  /T={t}  /TU={tu}  /MK/BC={bc}")
    pdf.close()


# ═══════════════════════════════════════════════════════════════
# CREATE TEST PDF
# ═══════════════════════════════════════════════════════════════

def create_test_pdf(path="test_jana_input.pdf"):
    """
    Creates one PDF that exercises all Jana non-AI corrections:

    1.4.3  - low contrast gray text (0.70 0.70 0.70 on white)
    1.4.11 - widget with low contrast border
    1.1.1  - form field with no /TU (control_missing_name → AI, skipped in non-AI test)
    2.5.3  - form field with wrong /TU (label_not_in_name → pure code fix)
    1.4.1  - explicit color instruction text (flagged)
    """
    pdf = pikepdf.new()

    page_dict = pikepdf.Dictionary(
        Type=pikepdf.Name("/Page"),
        MediaBox=pikepdf.Array([0, 0, 612, 792]),
    )
    page = pdf.make_indirect(page_dict)
    pdf.pages.append(pikepdf.Page(page))

    font = pdf.make_indirect(pikepdf.Dictionary(
        Type=pikepdf.Name("/Font"),
        Subtype=pikepdf.Name("/Type1"),
        BaseFont=pikepdf.Name("/Helvetica"),
    ))

    page["/Resources"] = pikepdf.Dictionary(
        Font=pikepdf.Dictionary(F1=font)
    )

    content = b"""
q
0.70 0.70 0.70 rg
BT
/F1 18 Tf
50 740 Td
(This is low contrast text) Tj
ET
Q

q
0 0 0 rg
BT
/F1 14 Tf
50 700 Td
(First Name) Tj
50 650 Td
(Email Address) Tj
50 600 Td
(Last Name) Tj
50 550 Td
(Phone Number) Tj
ET
Q

q
1 0 0 rg
BT
/F1 16 Tf
50 500 Td
(Required fields are marked in red.) Tj
ET
Q

q
0.8 0.8 0.8 RG
1 w
150 525 150 20 re
S
Q
"""
    page["/Contents"] = pdf.make_stream(content)

    # ── 1.1.1 control_missing_name: has /T but no /TU ────────────────────
    field_no_tu = pdf.make_indirect(pikepdf.Dictionary(
        Type=pikepdf.Name("/Annot"),
        Subtype=pikepdf.Name("/Widget"),
        FT=pikepdf.Name("/Tx"),
        T=pikepdf.String("first_name"),
        Rect=pikepdf.Array([150, 675, 300, 695]),
    ))

    # ── 2.5.3 label_not_in_name: has /TU but wrong value ─────────────────
    field_wrong_tu = pdf.make_indirect(pikepdf.Dictionary(
        Type=pikepdf.Name("/Annot"),
        Subtype=pikepdf.Name("/Widget"),
        FT=pikepdf.Name("/Tx"),
        T=pikepdf.String("email_internal"),
        TU=pikepdf.String("Contact"),
        Rect=pikepdf.Array([150, 625, 300, 645]),
    ))

    # ── good field: already correct ───────────────────────────────────────
    field_good = pdf.make_indirect(pikepdf.Dictionary(
        Type=pikepdf.Name("/Annot"),
        Subtype=pikepdf.Name("/Widget"),
        FT=pikepdf.Name("/Tx"),
        T=pikepdf.String("last_name"),
        TU=pikepdf.String("Last Name"),
        Rect=pikepdf.Array([150, 575, 300, 595]),
    ))

    # ── 1.4.11 widget with low contrast border ────────────────────────────
    # border color set to light gray via /MK /BC → will fail 3:1 against white
    field_low_border = pdf.make_indirect(pikepdf.Dictionary(
        Type=pikepdf.Name("/Annot"),
        Subtype=pikepdf.Name("/Widget"),
        FT=pikepdf.Name("/Tx"),
        T=pikepdf.String("phone_number"),
        Rect=pikepdf.Array([150, 525, 300, 545]),
        MK=pikepdf.Dictionary(
            BC=pikepdf.Array([
                pikepdf.Real(0.8),
                pikepdf.Real(0.8),
                pikepdf.Real(0.8),
            ])
        )
    ))

    pdf.Root["/AcroForm"] = pdf.make_indirect(pikepdf.Dictionary(
        Fields=pikepdf.Array([field_no_tu, field_wrong_tu, field_good, field_low_border])
    ))
    page["/Annots"] = pikepdf.Array([field_no_tu, field_wrong_tu, field_good, field_low_border])

    pdf.save(path)
    pdf.close()
    print(f"✅ Created {path}")


# ═══════════════════════════════════════════════════════════════
# TEST 1: 2.5.3 label_not_in_name — pure code fix
# ═══════════════════════════════════════════════════════════════

def test_2_5_3(doc_json, issues, input_path):
    print("\n" + "="*50)
    print("TEST: 2.5.3 label_not_in_name")
    print("="*50)
    output_path = "test_2_5_3_corrected.pdf"

    fields = doc_json.get("document", {}).get("interactivity", {}).get("acroform_fields", [])
    print("\n=== ACROFORM FIELDS ===")
    for f in fields:
        print(f"  id={f.get('id')} name={f.get('name')} tooltip={f.get('tooltip')}")

    email_field = next((f for f in fields if f.get("name") == "email_internal"), None)

    test_issues = list(issues)
    if email_field:
        test_issues.append({
            "criterion": "2.5.3",
            "issue": "label_not_in_name",
            "severity": "high",
            "location": {
                "field_id": email_field.get("id"),
                "widget_id": None,
                "visible_label": "Email Address",
                "programmatic_name": "Contact",
            },
        })
        print(f"\n✅ Injected 2.5.3 issue for field: {email_field.get('id')}")
    else:
        print("⚠️ email_internal not found")

    report = apply_corrections(
        original_pdf_path=input_path,
        issues=test_issues,
        doc_json=doc_json,
        output_path=output_path,
    )
    print_relevant(report, ["2.5.3"])
    verify_fields(output_path)

    # assert
    pdf = pikepdf.open(output_path)
    acroform = pdf.Root.get("/AcroForm")
    for ref in acroform.get("/Fields", []):
        obj = pdf.get_object(ref.objgen)
        if str(obj.get("/T", "")) == "email_internal":
            tu = str(obj.get("/TU", ""))
            assert tu == "Email Address", f"❌ Expected 'Email Address' got '{tu}'"
            print(f"\n✅ 2.5.3 PASSED: /TU correctly set to '{tu}'")
    pdf.close()


# ═══════════════════════════════════════════════════════════════
# TEST 2: 1.4.3 contrast — deterministic math fix
# ═══════════════════════════════════════════════════════════════

def test_1_4_3(doc_json, input_path):
    print("\n" + "="*50)
    print("TEST: 1.4.3 insufficient_text_contrast")
    print("="*50)
    output_path = "test_1_4_3_corrected.pdf"

    spans = doc_json.get("document", {}).get("text_spans", [])
    print(f"\n=== TEXT SPANS ({len(spans)} total) ===")

    if not spans:
        print("  ❌ No text spans found")
        return

    chosen_span = None
    for s in spans:
        fg = s.get("color", {}).get("fill_rgb")
        bg = s.get("background_estimate", {}).get("bg_rgb")
        if fg and bg:
            chosen_span = s
            print(f"  Using span: id={s.get('id')} text={repr(s.get('text'))} fg={fg} bg={bg}")
            break

    if not chosen_span:
        print("  ❌ No span with fg+bg found")
        return

    test_issues = [{
        "criterion": "1.4.3",
        "issue": "insufficient_text_contrast",
        "severity": "high",
        "location": {
            "span_id": chosen_span.get("id"),
            "contrast_ratio": 2.1,
        },
    }]

    report = apply_corrections(
        original_pdf_path=input_path,
        issues=test_issues,
        doc_json=doc_json,
        output_path=output_path,
    )
    print_relevant(report, ["1.4.3"])

    fixed = [r for r in report["corrections"] if r["criterion"] == "1.4.3" and r["status"] == "fixed"]
    flagged = [r for r in report["corrections"] if r["criterion"] == "1.4.3" and r["status"] == "flagged_manual"]

    if fixed:
        print(f"\n✅ 1.4.3 FIXED: color was rewritten in content stream")
    elif flagged:
        print(f"\n⚠️  1.4.3 FLAGGED: color computed but could not be written safely")
        print(f"   Detail: {flagged[0].get('detail', '')[:120]}")
    else:
        print(f"\n❌ 1.4.3 UNEXPECTED result")


# ═══════════════════════════════════════════════════════════════
# TEST 3: 1.4.11 widget border contrast — pure code fix
# ═══════════════════════════════════════════════════════════════

def test_1_4_11(doc_json, input_path):
    print("\n" + "="*50)
    print("TEST: 1.4.11 insufficient_non_text_contrast_ui_component")
    print("="*50)
    output_path = "test_1_4_11_corrected.pdf"

    widgets = doc_json.get("document", {}).get("widgets", [])
    print(f"\n=== WIDGETS ({len(widgets)} total) ===")
    for w in widgets:
        print(f"  id={w.get('id')} field_name={w.get('field_name')} ntc={w.get('non_text_contrast')}")

    phone_widget = next((w for w in widgets if w.get("field_name") == "phone_number"), None)

    if not phone_widget:
        print("⚠️ phone_number widget not found in doc_json")
        return

    test_issues = [{
        "criterion": "1.4.11",
        "issue": "insufficient_non_text_contrast_ui_component",
        "severity": "high",
        "location": {
            "widget_id": phone_widget.get("id"),
            "page": phone_widget.get("page_index"),
        },
    }]

    report = apply_corrections(
        original_pdf_path=input_path,
        issues=test_issues,
        doc_json=doc_json,
        output_path=output_path,
    )
    print_relevant(report, ["1.4.11"])
    verify_fields(output_path)
    print("\nNOTE:")
    print("  The fixer changed the widget /MK /BC value.")
    print("  Some PDF viewers may not visually update form borders unless the widget appearance stream is regenerated.")
    print("  Check the verification line for phone_number:")
    print("  /T=phone_number should have /MK/BC around [0.5765, 0.5765, 0.5765]")


# ═══════════════════════════════════════════════════════════════
# TEST 4: 1.4.1 color only — flagged only
# ═══════════════════════════════════════════════════════════════

def test_1_4_1(doc_json, issues, input_path):
    print("\n" + "="*50)
    print("TEST: 1.4.1 color only instructions — flagged")
    print("="*50)
    output_path = "test_1_4_1_corrected.pdf"

    color_issues = [i for i in issues if i["criterion"] == "1.4.1"]
    print(f"\n=== 1.4.1 ISSUES DETECTED ({len(color_issues)}) ===")
    for i in color_issues:
        print(f"  [{i['severity'].upper()}] {i['issue']}")

    report = apply_corrections(
        original_pdf_path=input_path,
        issues=issues,
        doc_json=doc_json,
        output_path=output_path,
    )
    print_relevant(report, ["1.4.1"])

    flagged = [r for r in report["corrections"] if r["criterion"] == "1.4.1" and r["status"] == "flagged_manual"]
    if flagged:
        print(f"\n✅ 1.4.1 correctly flagged {len(flagged)} issue(s) for manual review")
    else:
        print(f"\n⚠️  No 1.4.1 issues were flagged — check if detector found the color instruction text")


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("\n" + "="*50)
    print(" JANA NON-AI CORRECTION TESTS")
    print("="*50)

    input_pdf = "test_jana_input.pdf"
    create_test_pdf(input_pdf)

    doc_json = extract_document_json(input_pdf)
    issues   = run_wcag_detector(doc_json)

    print(f"\n=== ALL DETECTED ISSUES ===")
    for i in issues:
        if i["severity"] not in {"pass", "not_applicable"}:
            print(f"  [{i['severity'].upper()}] {i['criterion']} - {i['issue']}")

    test_2_5_3(doc_json, issues, input_pdf)
    test_1_4_3(doc_json, input_pdf)
    test_1_4_11(doc_json, input_pdf)
    test_1_4_1(doc_json, issues, input_pdf)

    print("\n✅ All non-AI tests finished")