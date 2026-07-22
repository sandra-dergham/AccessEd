from typing import Any, Dict
import pikepdf


def extract_interactivity_pikepdf(pdf_path: str) -> Dict[str, Any]:
    """
    Extract interactivity signals needed for WCAG 2.1.x / 3.3.x / 4.1.2 checks.
    """
    result: Dict[str, Any] = {
        "has_javascript":      False,
        "javascript_triggers": [],
        "has_acroform":        False,
        "acroform_fields":     [],
        "tab_order":           [],
        "has_tab_order":       False,
    }

    try:
        with pikepdf.open(pdf_path) as pdf:
            root = pdf.Root

            try:
                if "/OpenAction" in root:
                    action = root["/OpenAction"]
                    if isinstance(action, pikepdf.Dictionary):
                        s = action.get("/S")
                        if s and str(s) in ("/JavaScript", "/JS"):
                            result["has_javascript"] = True
                            result["javascript_triggers"].append({
                                "trigger": "OpenAction", "location": "document"
                            })
            except Exception:
                pass

            try:
                if "/AA" in root:
                    aa = root["/AA"]
                    if isinstance(aa, pikepdf.Dictionary):
                        for key in aa.keys():
                            entry = aa[key]
                            if isinstance(entry, pikepdf.Dictionary):
                                s = entry.get("/S")
                                if s and str(s) in ("/JavaScript", "/JS"):
                                    result["has_javascript"] = True
                                    result["javascript_triggers"].append({
                                        "trigger":  f"DocumentAA/{str(key)[1:]}",
                                        "location": "document"
                                    })
            except Exception:
                pass

            try:
                if "/Names" in root:
                    names = root["/Names"]
                    if isinstance(names, pikepdf.Dictionary) and "/JavaScript" in names:
                        result["has_javascript"] = True
                        result["javascript_triggers"].append({
                            "trigger": "Names/JavaScript", "location": "document"
                        })
            except Exception:
                pass

            try:
                for page_index, page in enumerate(pdf.pages):
                    if "/AA" in page:
                        aa = page["/AA"]
                        if isinstance(aa, pikepdf.Dictionary):
                            for key in aa.keys():
                                entry = aa[key]
                                if isinstance(entry, pikepdf.Dictionary):
                                    s = entry.get("/S")
                                    if s and str(s) in ("/JavaScript", "/JS"):
                                        result["has_javascript"] = True
                                        result["javascript_triggers"].append({
                                            "trigger":  f"PageAA/{str(key)[1:]}",
                                            "location": f"page_{page_index}"
                                        })
            except Exception:
                pass

            try:
                for page_index, page in enumerate(pdf.pages):
                    tabs_val = None
                    if "/Tabs" in page:
                        tabs_val = str(page["/Tabs"])[1:]
                        result["has_tab_order"] = True
                    result["tab_order"].append({
                        "page_index": page_index,
                        "tabs":       tabs_val
                    })
            except Exception:
                pass

            result["submit_actions"] = []
            try:
                if "/AcroForm" in root:
                    acroform   = root["/AcroForm"]
                    if isinstance(acroform, pikepdf.Dictionary):
                        fields_arr = acroform.get("/Fields")
                        if fields_arr and isinstance(fields_arr, pikepdf.Array):

                            def find_submit(field_obj, counter=[0]):
                                if not isinstance(field_obj, pikepdf.Dictionary):
                                    return
                                try:
                                    if "/A" in field_obj:
                                        action = field_obj["/A"]
                                        if isinstance(action, pikepdf.Dictionary):
                                            s = action.get("/S")
                                            if s and str(s) == "/SubmitForm":
                                                url = None
                                                try:
                                                    f_entry = action.get("/F")
                                                    if f_entry and isinstance(f_entry, pikepdf.Dictionary):
                                                        url = str(f_entry.get("/FS") or f_entry.get("/F") or "")
                                                except Exception:
                                                    pass
                                                result["submit_actions"].append({
                                                    "field_id": f"field_{counter[0]}",
                                                    "action":   "SubmitForm",
                                                    "url":      url,
                                                })
                                except Exception:
                                    pass
                                try:
                                    if "/Kids" in field_obj:
                                        kids = field_obj["/Kids"]
                                        if isinstance(kids, pikepdf.Array):
                                            for kid in kids:
                                                try:
                                                    obj = kid if isinstance(kid, pikepdf.Dictionary) else kid.get_object()
                                                    counter[0] += 1
                                                    find_submit(obj, counter)
                                                except Exception:
                                                    pass
                                except Exception:
                                    pass

                            for field_ref in fields_arr:
                                try:
                                    obj = field_ref if isinstance(field_ref, pikepdf.Dictionary) else field_ref.get_object()
                                    find_submit(obj)
                                except Exception:
                                    pass
            except Exception:
                pass

            result["has_submit_action"] = len(result["submit_actions"]) > 0

            try:
                if "/AcroForm" not in root:
                    return result

                acroform = root["/AcroForm"]
                if not isinstance(acroform, pikepdf.Dictionary):
                    return result

                result["has_acroform"] = True

                fields_array = acroform.get("/Fields")
                if not fields_array or not isinstance(fields_array, pikepdf.Array):
                    return result

                field_counter = 0

                def process_field(field_obj: pikepdf.Dictionary, page_index):
                    nonlocal field_counter

                    if "/Kids" in field_obj:
                        kids = field_obj["/Kids"]
                        if isinstance(kids, pikepdf.Array):
                            for kid in kids:
                                try:
                                    obj = kid if isinstance(kid, pikepdf.Dictionary) else kid.get_object()
                                    process_field(obj, page_index)
                                except Exception:
                                    pass
                        return

                    field_id = f"field_{field_counter}"
                    field_counter += 1

                    ft = None
                    try:
                        if "/FT" in field_obj:
                            ft = str(field_obj["/FT"])[1:]
                    except Exception:
                        pass

                    name = None
                    try:
                        if "/T" in field_obj:
                            name = str(field_obj["/T"])
                    except Exception:
                        pass

                    tooltip = None
                    try:
                        if "/TU" in field_obj:
                            tooltip = str(field_obj["/TU"])
                    except Exception:
                        pass

                    ff = 0
                    try:
                        if "/Ff" in field_obj:
                            ff = int(field_obj["/Ff"])
                    except Exception:
                        pass

                    read_only = bool(ff & 1)
                    required  = bool(ff & 2)

                    pg_index = page_index
                    try:
                        if "/P" in field_obj:
                            p_ref = field_obj["/P"]
                            for i, pg in enumerate(pdf.pages):
                                if pg.objgen == p_ref.objgen:
                                    pg_index = i
                                    break
                    except Exception:
                        pass

                    field_aa = {}
                    try:
                        if "/AA" in field_obj:
                            aa = field_obj["/AA"]
                            if isinstance(aa, pikepdf.Dictionary):
                                for aa_key in aa.keys():
                                    entry = aa[aa_key]
                                    is_js = False
                                    if isinstance(entry, pikepdf.Dictionary):
                                        s = entry.get("/S")
                                        if s and str(s) in ("/JavaScript", "/JS"):
                                            is_js = True
                                    field_aa[str(aa_key)[1:]] = {"has_javascript": is_js}
                    except Exception:
                        pass

                    appearance_state = None
                    try:
                        if "/AS" in field_obj:
                            appearance_state = str(field_obj["/AS"])[1:]
                    except Exception:
                        pass

                    result["acroform_fields"].append({
                        "id":                 field_id,
                        "name":               name,
                        "type":               ft,
                        "flags":              ff,
                        "read_only":          read_only,
                        "required":           required,
                        "tooltip":            tooltip,
                        "page_index":         pg_index,
                        "validation_actions": field_aa,
                        "appearance_state":   appearance_state,
                    })

                for field_ref in fields_array:
                    try:
                        obj = field_ref if isinstance(field_ref, pikepdf.Dictionary) else field_ref.get_object()
                        process_field(obj, None)
                    except Exception:
                        pass

            except Exception:
                pass

    except Exception as e:
        result["error"] = f"pikepdf failed: {type(e).__name__}: {e}"

    return result

