from typing import Any, Dict, List

import pikepdf

def _flatten_structure_nodes(tree: Any) -> List[Dict[str, Any]]:
    nodes: List[Dict[str, Any]] = []

    def walk(node):
        if not isinstance(node, dict):
            return
        nodes.append(node)
        for child in node.get("children", []):
            if isinstance(child, dict):
                walk(child)

    if isinstance(tree, list):
        for root in tree:
            walk(root)

    return nodes



def _pike_to_py(obj):
    try:
        if isinstance(obj, pikepdf.Name):
            return str(obj)[1:]
        if isinstance(obj, (pikepdf.String, str)):
            return str(obj)
        if isinstance(obj, (int, float, bool)):
            return obj
        if obj is None:
            return None
        if isinstance(obj, pikepdf.Array):
            return [_pike_to_py(x) for x in obj]
        if isinstance(obj, pikepdf.Dictionary):
            out = {}
            for k, v in obj.items():
                out[str(k)[1:] if isinstance(k, pikepdf.Name) else str(k)] = _pike_to_py(v)
            return out
        return str(obj)
    except Exception:
        return str(obj)
    

def extract_structure_pikepdf(pdf_path: str):
    """
    Extract /Lang, /StructTreeRoot presence, /RoleMap, and simplified structure tree.
    """
    result = {
        "has_tags":   False,
        "lang":       None,
        "role_map":   None,
        "tree":       None,
        "validation": {"errors": [], "notes": []}
    }

    try:
        with pikepdf.open(pdf_path) as pdf:
            root = pdf.Root

            try:
                if "/Lang" in root:
                    lang_val = str(root["/Lang"]).strip()
                    if lang_val.startswith("(") and lang_val.endswith(")"):
                        lang_val = lang_val[1:-1].strip()
                    result["lang"] = lang_val or None
            except Exception:
                result["validation"]["notes"].append("Could not read /Lang")

            if "/StructTreeRoot" not in root:
                result["validation"]["errors"].append(
                    "No /StructTreeRoot (PDF is likely untagged)"
                )
                return result

            result["has_tags"]  = True
            struct_root = root["/StructTreeRoot"]

            try:
                if "/RoleMap" in struct_root:
                    result["role_map"] = _pike_to_py(struct_root["/RoleMap"])
            except Exception:
                result["validation"]["notes"].append("Could not read /RoleMap")

            if "/K" not in struct_root:
                result["validation"]["errors"].append("StructTreeRoot has no /K children")
                result["tree"] = []
                return result

            kids = struct_root["/K"]
            if not isinstance(kids, pikepdf.Array):
                kids = pikepdf.Array([kids])

            nodes        = []
            node_counter = 0

            def walk(node, depth=0):
                nonlocal node_counter
                node_id = f"node_{node_counter}"
                node_counter += 1

                role = None
                try:
                    if isinstance(node, pikepdf.Dictionary) and "/S" in node:
                        role = str(node["/S"])[1:]
                except Exception:
                    role = None

                out_node = {
                    "id":              node_id,
                    "role":            role,
                    "depth":           depth,
                    "children":        [],
                    "alt":             None,
                    "actual_text":     None,
                    "mcids":           [],
                    "page_object_ref": None,
                }

                try:
                    if isinstance(node, pikepdf.Dictionary) and "/Alt" in node:
                        out_node["alt"] = str(node["/Alt"])
                except Exception:
                    pass

                try:
                    if isinstance(node, pikepdf.Dictionary) and "/ActualText" in node:
                        out_node["actual_text"] = str(node["/ActualText"])
                except Exception:
                    pass

                try:
                    if isinstance(node, pikepdf.Dictionary) and "/Pg" in node:
                        pg = node["/Pg"]
                        if hasattr(pg, "objgen"):
                            out_node["page_object_ref"] = pg.objgen
                except Exception:
                    pass

                try:
                    if isinstance(node, pikepdf.Dictionary) and "/K" in node:
                        child = node["/K"]

                        def handle_k_item(item):
                            if isinstance(item, int):
                                out_node["mcids"].append(item)
                                out_node["children"].append({"type": "mcid", "value": int(item)})
                                return

                            if isinstance(item, pikepdf.Dictionary):
                                if "/MCID" in item:
                                    try:
                                        mcid_val = int(item["/MCID"])
                                        out_node["mcids"].append(mcid_val)
                                    except Exception:
                                        mcid_val = None

                                    pg_ref = None
                                    try:
                                        if "/Pg" in item and hasattr(item["/Pg"], "objgen"):
                                            pg_ref = item["/Pg"].objgen
                                    except Exception:
                                        pass

                                    out_node["children"].append({
                                        "type":            "mcr",
                                        "mcid":            mcid_val,
                                        "page_object_ref": pg_ref,
                                    })
                                    return

                                out_node["children"].append(walk(item, depth + 1))
                                return

                            out_node["children"].append({
                                "type":  "leaf",
                                "value": _pike_to_py(item)
                            })

                        if isinstance(child, pikepdf.Array):
                            for c in child:
                                handle_k_item(c)
                        else:
                            handle_k_item(child)

                except Exception:
                    result["validation"]["notes"].append(
                        f"Failed walking children at depth {depth}"
                    )

                return out_node

            for k in kids:
                if isinstance(k, pikepdf.Dictionary):
                    nodes.append(walk(k, 0))
                else:
                    nodes.append({"type": "leaf", "value": _pike_to_py(k)})

            result["tree"] = nodes
            return result

    except Exception as e:
        result["validation"]["errors"].append(f"pikepdf failed: {type(e).__name__}: {e}")
        return result


def extract_figure_nodes_from_structure(structure: Dict[str, Any]) -> List[Dict[str, Any]]:
    figures:    List[Dict[str, Any]] = []
    tree        = structure.get("tree")
    flat_nodes  = _flatten_structure_nodes(tree)

    for node in flat_nodes:
        if node.get("role") != "Figure":
            continue

        alt         = node.get("alt")
        actual_text = node.get("actual_text")

        figures.append({
            "id":           node.get("id"),
            "role":         "Figure",
            "alt":          alt,
            "actual_text":  actual_text,
            "is_decorative": alt is not None and str(alt).strip() == "",
            "depth":        node.get("depth"),
            "mcids":        node.get("mcids", []),
            "page_object_ref": node.get("page_object_ref"),
            "children":     node.get("children", []),
        })

    return figures
