import logging
import pikepdf
from ....utils.color_helper import(
    contrast_ratio,
    _blend_toward,
)

logger = logging.getLogger(__name__)

def _find_acroform_field_by_bbox(
    pdf: pikepdf.Pdf,
    page_index: int,
    bbox: list,
    tolerance: float = 5.0,
) -> "pikepdf.Dictionary | None":
    """
    Fallback field lookup when /T is missing.
    Matches by comparing the field's /Rect against the widget bbox on the given page.
    """
    try:
        acroform = pdf.Root.get("/AcroForm")
        if acroform is None:
            return None
        fields = acroform.get("/Fields")
        if fields is None:
            return None

        if not bbox or len(bbox) < 4:
            return None

        target_page_obj = pdf.pages[page_index].obj

        # Convert PyMuPDF bbox (top-left origin) to PDF coords (bottom-left origin)
        try:
            page_height = float(pdf.pages[page_index].mediabox[3])
        except Exception:
            return None

        pdf_x0 = bbox[0]
        pdf_y0 = page_height - bbox[3]
        pdf_x1 = bbox[2]
        pdf_y1 = page_height - bbox[1]

        def close(a, b):
            return abs(a - b) < tolerance

        def check_node(node):
            if not isinstance(node, pikepdf.Dictionary):
                try:
                    node = node.get_object()
                except Exception:
                    return None
            # Check if this node has a /Rect that matches
            rect = node.get("/Rect")
            if rect is not None:
                try:
                    r = [float(x) for x in rect]
                    if (close(r[0], pdf_x0) and close(r[1], pdf_y0)
                            and close(r[2], pdf_x1) and close(r[3], pdf_y1)):
                        return node
                except Exception:
                    pass
            # Check /Kids (widget annotations under a field)
            kids = node.get("/Kids")
            if isinstance(kids, pikepdf.Array):
                for kid in kids:
                    found = check_node(kid)
                    if found is not None:
                        # Return the parent field node, not the kid widget
                        return node
            return None

        for field_ref in fields:
            found = check_node(field_ref)
            if found is not None:
                return found
    except Exception as exc:
        logger.debug("_find_acroform_field_by_bbox: %s", exc)
    return None



def _find_accessible_color(fg_rgb: list, bg_rgb: list, target_ratio: float) -> list | None:
        if contrast_ratio(fg_rgb, bg_rgb) >= target_ratio:
            return fg_rgb
        candidates = []
        for direction in ([0, 0, 0], [255, 255, 255]):
            for step in range(1, 101):
                candidate = _blend_toward(fg_rgb, direction, step / 100)
                if contrast_ratio(candidate, bg_rgb) >= target_ratio:
                    distance = sum(abs(candidate[i] - fg_rgb[i]) for i in range(3))
                    candidates.append((distance, candidate))
                    break
        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0])
        return candidates[0][1]


def _try_recolor_in_stream(
    pdf: pikepdf.Pdf,
    page_index: int,
    old_rgb: list[int],
    new_rgb: list[int],
) -> bool:
    try:
        import re
        import zlib

        if page_index is None:
            return False

        page     = pdf.pages[page_index]
        contents = page.get("/Contents")
        if contents is None:
            return False

        def to_pdf_float(v: int) -> float:
            return round(v / 255.0, 4)

        def close(a: float, b: float, tol: float = 0.02) -> bool:
            return abs(a - b) <= tol

        old_vals = [to_pdf_float(v) for v in old_rgb]
        new_vals = [to_pdf_float(v) for v in new_rgb]

        def make_op(suffix: bytes) -> bytes:
            return (
                f"{new_vals[0]:.4f} {new_vals[1]:.4f} {new_vals[2]:.4f} ".encode()
                + suffix
            )

        new_rg = make_op(b"rg")
        new_RG = make_op(b"RG")

        color_re = re.compile(
            rb"(?P<r>(?:\d*\.\d+|\d+))\s+"
            rb"(?P<g>(?:\d*\.\d+|\d+))\s+"
            rb"(?P<b>(?:\d*\.\d+|\d+))\s+"
            rb"(?P<op>rg|RG)\b"
        )
        bt_re = re.compile(rb"\bBT\b")
        et_re = re.compile(rb"\bET\b")

        def read_stream(s):
            raw = s.read_bytes()
            try:
                decoded = s.read_bytes(decode_level=pikepdf.StreamDecodeLevel.all)
                return bytes(decoded), "pikepdf"
            except Exception:
                pass
            try:
                return zlib.decompress(raw), "zlib"
            except Exception:
                pass
            return raw, "raw"

        def write_stream(s, data: bytes, decode_mode: str):
            if decode_mode in ("pikepdf", "zlib"):
                s.write(zlib.compress(data), filter=pikepdf.Name("/FlateDecode"))
            else:
                s.write(data)

        def matches_target(m) -> bool:
            try:
                r = float(m.group("r"))
                g = float(m.group("g"))
                b = float(m.group("b"))
            except ValueError:
                return False
            return close(r, old_vals[0]) and close(g, old_vals[1]) and close(b, old_vals[2])

        def replace_in_stream(stream) -> bool:
            data, decode_mode = read_stream(stream)

            bt_positions = [m.start() for m in bt_re.finditer(data)]
            et_positions = [m.start() for m in et_re.finditer(data)]

            candidates: set[int] = set()

            if bt_positions:
                # Build BT→ET ranges
                bt_et_ranges: list[tuple[int, int]] = []
                ei = 0
                for bt in bt_positions:
                    while ei < len(et_positions) and et_positions[ei] <= bt:
                        ei += 1
                    et_end = (
                        et_positions[ei] + 2
                        if ei < len(et_positions)
                        else len(data)
                    )
                    bt_et_ranges.append((bt, et_end))

                # Pass 1 — rg/RG inside BT…ET
                for m in color_re.finditer(data):
                    if not matches_target(m):
                        continue
                    for bt_start, et_end in bt_et_ranges:
                        if bt_start <= m.start() < et_end:
                            candidates.add(m.start())
                            break

                # Pass 2 — last rg/RG before each BT (inherited color state)
                for bt_start, _ in bt_et_ranges:
                    pre = [
                        m for m in color_re.finditer(data)
                        if m.end() <= bt_start
                    ]
                    for m in reversed(pre):
                        if matches_target(m):
                            candidates.add(m.start())
                        break  

            else:
    
                for m in color_re.finditer(data):
                    if matches_target(m):
                        candidates.add(m.start())

            if not candidates:
                return False

            all_match_map = {m.start(): m for m in color_re.finditer(data)}
            to_replace = sorted(
                (m for pos, m in all_match_map.items() if pos in candidates),
                key=lambda m: m.start(),
                reverse=True,
            )

            new_data = bytearray(data)
            for m in to_replace:
                repl = new_rg if m.group("op") == b"rg" else new_RG
                new_data[m.start():m.end()] = repl

            write_stream(stream, bytes(new_data), decode_mode)
            return True

        changed = False

        if isinstance(contents, pikepdf.Stream):
            changed = replace_in_stream(contents)
        elif isinstance(contents, pikepdf.Array):
            for item in contents:
                try:
                    stream = item.get_object()
                except Exception:
                    stream = item
                if isinstance(stream, pikepdf.Stream):
                    if replace_in_stream(stream):
                        changed = True

        return changed

    except Exception as exc:
        logger.debug("_try_recolor_in_stream failed: %s", exc, exc_info=True)
        return False
    
    